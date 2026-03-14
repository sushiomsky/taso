"""
TASO – DevAgent

Responsible for code improvement proposals and patch generation.

Bus topics consumed:
  dev.update_self     – analyse own codebase and propose improvements
  dev.generate_patch  – generate a patch for a specific file/issue

Bus topics published:
  coordinator.result.<task_id>
  memory.store
"""

from __future__ import annotations

import asyncio
import difflib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from config.settings import settings

log = get_logger("agent")


class DevAgent(BaseAgent):
    name = "dev"
    description = "Code improvement, patch generation, and self-improvement proposals."

    SYSTEM_PROMPT = (
        "You are a senior Python software engineer specialising in security-focused "
        "applications. Analyse code for bugs, inefficiencies, and security issues. "
        "Propose concrete, minimal, backwards-compatible improvements."
    )

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("dev.update_self",    self._handle_update_self)
        self._bus.subscribe("dev.generate_patch", self._handle_generate_patch)
        self._bus.subscribe("dev.review_code",    self._handle_review_code)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_update_self(self, msg: BusMessage) -> None:
        task_id = msg.payload.get("task_id", "")
        log.info("DevAgent: update_self initiated")

        if not settings.SELF_IMPROVE_ENABLED:
            result = {
                "task_id": task_id,
                "status":  "disabled",
                "message": "Self-improvement is disabled (SELF_IMPROVE_ENABLED=false).",
            }
            await self._reply(msg, result)
            return

        # Discover Python files (excluding protected modules)
        files = self._discover_files()
        proposals: List[Dict] = []

        for fpath in files[:10]:  # limit per run
            proposal = await self._analyse_file(fpath)
            if proposal.get("improvements"):
                proposals.append(proposal)

        result = {
            "task_id":   task_id,
            "proposals": proposals,
            "count":     len(proposals),
            "note":      "Proposals require manual review before applying.",
        }
        await self._reply(msg, result)

    async def _handle_generate_patch(self, msg: BusMessage) -> None:
        task_id   = msg.payload.get("task_id", "")
        file_path = msg.payload.get("file_path", "")
        issue     = msg.payload.get("issue", "")

        log.info(f"DevAgent: generate_patch for {file_path}")

        path = Path(file_path)
        if not path.exists():
            await self._reply(msg, {"task_id": task_id, "error": "File not found."})
            return

        if self._is_protected(path):
            await self._reply(msg, {
                "task_id": task_id,
                "error":   f"File is in a protected module: {file_path}",
            })
            return

        original = path.read_text(errors="ignore")
        prompt = (
            f"File: {file_path}\n\nIssue to fix: {issue}\n\n"
            f"Original code:\n```python\n{original[:6000]}\n```\n\n"
            "Provide ONLY the corrected complete file content. "
            "Do not include any explanation, only the fixed code."
        )
        fixed_code = await self.llm_query(prompt, system=self.SYSTEM_PROMPT)

        # Extract code block if wrapped in markdown
        fixed_code = _extract_code_block(fixed_code)

        patch = _make_patch(original, fixed_code, file_path)
        patch_lines = patch.count("\n")

        if patch_lines > settings.MAX_PATCH_LINES:
            await self._reply(msg, {
                "task_id":     task_id,
                "error":       f"Patch too large ({patch_lines} lines > {settings.MAX_PATCH_LINES}).",
                "patch_lines": patch_lines,
            })
            return

        result = {
            "task_id":    task_id,
            "file":       file_path,
            "patch":      patch,
            "patch_lines": patch_lines,
            "status":     "pending_review",
        }
        await self._reply(msg, result)

    async def _handle_review_code(self, msg: BusMessage) -> None:
        task_id = msg.payload.get("task_id", "")
        code    = msg.payload.get("code", "")
        context = msg.payload.get("context", "")

        prompt = (
            f"Context: {context}\n\n"
            f"Code to review:\n```python\n{code[:6000]}\n```\n\n"
            "Provide: 1) code quality score (1-10), 2) bugs found, "
            "3) security issues, 4) performance issues, 5) improvement suggestions."
        )
        review = await self.llm_query(prompt, system=self.SYSTEM_PROMPT)

        result = {"task_id": task_id, "review": review, "context": context}
        await self._reply(msg, result)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _discover_files(self) -> List[Path]:
        """List Python source files excluding protected modules."""
        root = settings.GIT_REPO_PATH
        files = []
        for f in sorted(root.rglob("*.py")):
            if not self._is_protected(f):
                files.append(f)
        return files

    def _is_protected(self, path: Path) -> bool:
        path_str = str(path)
        return any(p in path_str for p in settings.PROTECTED_MODULES)

    async def _analyse_file(self, fpath: Path) -> Dict[str, Any]:
        code = fpath.read_text(errors="ignore")
        if len(code) < 10:
            return {"file": str(fpath), "improvements": []}

        prompt = (
            f"File: {fpath}\n\n```python\n{code[:4000]}\n```\n\n"
            "List up to 3 specific, high-value improvements. "
            "Format as JSON array: "
            '[{"issue": "...", "suggestion": "...", "priority": "high|medium|low"}]'
        )
        raw = await self.llm_query(prompt, system=self.SYSTEM_PROMPT)
        improvements = _parse_json_list(raw)

        return {
            "file":         str(fpath),
            "improvements": improvements,
        }

    async def _reply(self, msg: BusMessage, result: Dict) -> None:
        if msg.reply_to:
            await self._bus.publish(
                BusMessage(
                    topic=msg.reply_to,
                    sender=self.name,
                    recipient=msg.sender,
                    payload=result,
                )
            )

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use — code improvement."""
        prompt = description
        if context:
            prompt = f"{context}\n\nTask: {description}"
        return await self.llm_query(prompt, system=self.SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def _make_patch(original: str, modified: str, filename: str) -> str:
    """Generate a unified diff patch."""
    orig_lines = original.splitlines(keepends=True)
    mod_lines  = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines, mod_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )
    return "".join(diff)


def _extract_code_block(text: str) -> str:
    """Strip markdown code fences if present."""
    import re
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    return match.group(1) if match else text


def _parse_json_list(text: str) -> List[Dict]:
    """Try to extract a JSON list from LLM output."""
    import re
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return []
