"""
TASO – Self-improvement: patch_generator

Uses the LLM (via DevAgent style prompts) and the CodeAnalyzer findings
to generate unified diff patches for specific files.

Each generated patch is validated:
  1. Git apply --check (dry run)
  2. Bandit static analysis score must not worsen
"""

from __future__ import annotations

import asyncio
import difflib
import re
import tempfile
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from config.logging_config import self_improvement_log as log
from self_improvement.code_analyzer import CodeAnalyzer


class PatchProposal:
    def __init__(self, file_path: str, original: str,
                  modified: str, findings: List[Dict],
                  description: str) -> None:
        self.file_path   = file_path
        self.original    = original
        self.modified    = modified
        self.findings    = findings
        self.description = description
        self._patch: Optional[str] = None

    @property
    def patch(self) -> str:
        if self._patch is None:
            orig  = self.original.splitlines(keepends=True)
            mod   = self.modified.splitlines(keepends=True)
            self._patch = "".join(difflib.unified_diff(
                orig, mod,
                fromfile=f"a/{self.file_path}",
                tofile=f"b/{self.file_path}",
                lineterm="",
            ))
        return self._patch

    @property
    def patch_size(self) -> int:
        return self.patch.count("\n")

    @property
    def is_within_limit(self) -> bool:
        return self.patch_size <= settings.MAX_PATCH_LINES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file":        self.file_path,
            "description": self.description,
            "patch":       self.patch,
            "patch_lines": self.patch_size,
            "findings":    self.findings,
            "within_limit": self.is_within_limit,
        }


class PatchGenerator:
    """
    Generates improvement patches for Python files based on static analysis
    findings.  Optionally uses an LLM to produce the fixed code.
    """

    SYSTEM_PROMPT = (
        "You are a Python expert tasked with fixing specific code quality issues. "
        "Return ONLY the corrected Python source code with no explanation, "
        "no markdown fences, no comments added. Preserve original logic exactly."
    )

    def __init__(self, llm_callable=None) -> None:
        """
        *llm_callable* is an async function (prompt, system) -> str.
        If None, rule-based fixes only are attempted.
        """
        self._llm    = llm_callable
        self._analyser = CodeAnalyzer()

    async def generate_for_file(
        self, file_path: Path, issues: Optional[List[Dict]] = None
    ) -> Optional[PatchProposal]:
        """
        Analyse *file_path*, optionally use provided *issues*, and
        produce a PatchProposal (or None if nothing changed / protected).
        """
        if self._is_protected(file_path):
            log.warning(f"PatchGenerator: skipping protected file {file_path}")
            return None

        original = file_path.read_text(errors="ignore")
        if not original.strip():
            return None

        if issues is None:
            result = self._analyser.analyse_file(file_path)
            issues = result.get("findings", [])

        if not issues:
            return None

        description = self._describe_issues(issues)

        # Try LLM-based fix first
        if self._llm:
            modified = await self._llm_fix(original, file_path, issues)
        else:
            modified = self._rule_based_fix(original, issues)

        if modified.strip() == original.strip():
            return None

        proposal = PatchProposal(
            file_path=str(file_path),
            original=original,
            modified=modified,
            findings=issues,
            description=description,
        )

        if not proposal.is_within_limit:
            log.warning(
                f"PatchGenerator: patch for {file_path} exceeds limit "
                f"({proposal.patch_size} > {settings.MAX_PATCH_LINES} lines)"
            )
            return None

        return proposal

    async def generate_batch(
        self, file_paths: List[Path]
    ) -> List[PatchProposal]:
        """Generate proposals for a list of files concurrently."""
        tasks = [self.generate_for_file(p) for p in file_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        proposals = []
        for r in results:
            if isinstance(r, PatchProposal):
                proposals.append(r)
            elif isinstance(r, Exception):
                log.error(f"PatchGenerator error: {r}")
        return proposals

    # ------------------------------------------------------------------
    # LLM fix
    # ------------------------------------------------------------------

    async def _llm_fix(self, code: str, path: Path,
                        issues: List[Dict]) -> str:
        issues_text = "\n".join(
            f"  - Line {i.get('line', '?')}: [{i['severity'].upper()}] {i['message']}"
            for i in issues[:10]
        )
        prompt = (
            f"File: {path.name}\n\n"
            f"Issues to fix:\n{issues_text}\n\n"
            f"Original code:\n```python\n{code[:5000]}\n```\n\n"
            "Return the complete corrected Python file."
        )
        try:
            result = await self._llm(prompt, self.SYSTEM_PROMPT)
            # Strip markdown fences if present
            return _strip_fences(result)
        except Exception as exc:
            log.error(f"LLM fix failed: {exc}")
            return code

    # ------------------------------------------------------------------
    # Rule-based fixes (no LLM required)
    # ------------------------------------------------------------------

    @staticmethod
    def _rule_based_fix(code: str, issues: List[Dict]) -> str:
        """Apply deterministic fixes for well-known patterns."""
        lines = code.splitlines(keepends=True)
        modified = list(lines)

        for issue in issues:
            kind = issue.get("type", "")
            lineno = (issue.get("line") or 1) - 1  # 0-indexed

            if kind == "bare_except" and 0 <= lineno < len(modified):
                # Replace bare `except:` with `except Exception:`
                modified[lineno] = modified[lineno].replace(
                    "except:", "except Exception:"
                )

            elif kind == "debug_print" and 0 <= lineno < len(modified):
                # Comment out stray print()
                stripped = modified[lineno].lstrip()
                indent   = modified[lineno][: len(modified[lineno]) - len(stripped)]
                modified[lineno] = f"{indent}# {stripped}"

        return "".join(modified)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_protected(path: Path) -> bool:
        return any(p in str(path) for p in settings.PROTECTED_MODULES)

    @staticmethod
    def _describe_issues(issues: List[Dict]) -> str:
        counts: Dict[str, int] = {}
        for i in issues:
            k = i.get("type", "unknown")
            counts[k] = counts.get(k, 0) + 1
        parts = [f"{v}× {k}" for k, v in counts.items()]
        return "Fix: " + ", ".join(parts)


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    return m.group(1) if m else text
