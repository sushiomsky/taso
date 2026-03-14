"""
TASO – Self-improvement: code_analyzer

Scans the codebase for potential improvements, bugs, and security
issues. Results feed the patch_generator.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from config.logging_config import self_improvement_log as log


class CodeAnalyzer:
    """
    Static code analysis for self-improvement candidate detection.

    Checks:
      • syntax errors
      • cyclomatic-complexity proxies (nested ifs/loops)
      • bare except clauses
      • hardcoded credentials pattern
      • missing type hints on public functions
      • TODO/FIXME markers
      • deprecated function calls (exec, eval, os.system)
    """

    DANGEROUS_CALLS = {"exec", "eval", "os.system", "subprocess.call",
                       "compile", "pickle.loads"}

    def analyse_file(self, path: Path) -> Dict[str, Any]:
        """Analyse a single Python file and return findings."""
        try:
            source = path.read_text(errors="ignore")
        except Exception as exc:
            return {"file": str(path), "error": str(exc), "findings": []}

        findings: List[Dict] = []
        findings += self._check_syntax(source, path)

        try:
            tree = ast.parse(source, filename=str(path))
            findings += self._check_ast(tree, source, path)
        except SyntaxError:
            pass

        findings += self._check_patterns(source, path)

        return {
            "file":      str(path),
            "findings":  findings,
            "loc":       source.count("\n") + 1,
            "score":     self._score(findings),
        }

    def analyse_repo(
        self, root: Optional[Path] = None, max_files: int = 100
    ) -> List[Dict[str, Any]]:
        """Analyse every Python file in *root* (default: project root)."""
        root = root or settings.GIT_REPO_PATH
        results = []

        for fpath in sorted(root.rglob("*.py"))[:max_files]:
            if self._is_excluded(fpath):
                continue
            result = self.analyse_file(fpath)
            if result.get("findings"):
                results.append(result)

        # Sort by most findings first
        return sorted(results, key=lambda r: len(r["findings"]), reverse=True)

    # ------------------------------------------------------------------
    # Individual checkers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_syntax(source: str, path: Path) -> List[Dict]:
        try:
            compile(source, str(path), "exec")
            return []
        except SyntaxError as exc:
            return [{
                "type":     "syntax_error",
                "severity": "critical",
                "line":     exc.lineno,
                "message":  str(exc),
            }]

    def _check_ast(self, tree: ast.AST, source: str,
                    path: Path) -> List[Dict]:
        findings = []
        lines    = source.splitlines()

        for node in ast.walk(tree):
            # Bare except
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                findings.append({
                    "type":     "bare_except",
                    "severity": "medium",
                    "line":     node.lineno,
                    "message":  "Bare `except:` catches all exceptions including KeyboardInterrupt.",
                })

            # Dangerous function calls
            if isinstance(node, ast.Call):
                name = self._call_name(node)
                if name in self.DANGEROUS_CALLS:
                    findings.append({
                        "type":     "dangerous_call",
                        "severity": "high",
                        "line":     node.lineno,
                        "message":  f"Use of potentially dangerous function: {name}()",
                    })

            # Deep nesting (proxy for high complexity)
            if isinstance(node, (ast.For, ast.While, ast.If)):
                depth = self._nesting_depth(node)
                if depth >= 4:
                    findings.append({
                        "type":     "high_complexity",
                        "severity": "low",
                        "line":     node.lineno,
                        "message":  f"Nesting depth {depth} – consider refactoring.",
                    })

            # Public functions without type hints
            if isinstance(node, ast.FunctionDef):
                if (not node.name.startswith("_")
                        and not node.returns
                        and not all(a.annotation for a in node.args.args)):
                    findings.append({
                        "type":     "missing_type_hints",
                        "severity": "low",
                        "line":     node.lineno,
                        "message":  f"Public function `{node.name}` lacks type hints.",
                    })

        return findings

    @staticmethod
    def _check_patterns(source: str, path: Path) -> List[Dict]:
        findings = []
        patterns = [
            (re.compile(r'(?i)(password|secret|token|api[_-]?key)\s*=\s*["\'][^"\']{6,}'),
             "hardcoded_credential", "critical",
             "Potential hardcoded credential detected."),
            (re.compile(r"#\s*(TODO|FIXME|HACK|XXX)\b", re.I),
             "todo_marker", "low",
             "Outstanding TODO/FIXME marker."),
            (re.compile(r"print\s*\("),
             "debug_print", "low",
             "Debug print() statement – use logging instead."),
        ]
        for i, line in enumerate(source.splitlines(), 1):
            for pat, kind, sev, msg in patterns:
                if pat.search(line):
                    findings.append({
                        "type":     kind,
                        "severity": sev,
                        "line":     i,
                        "message":  msg,
                        "snippet":  line.strip()[:80],
                    })
        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            parts = []
            n: Any = node.func
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name):
                parts.append(n.id)
            return ".".join(reversed(parts))
        return ""

    @staticmethod
    def _nesting_depth(node: ast.AST, depth: int = 0) -> int:
        max_d = depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While, ast.If, ast.With,
                                   ast.Try, ast.ExceptHandler)):
                d = CodeAnalyzer._nesting_depth(child, depth + 1)
                max_d = max(max_d, d)
        return max_d

    @staticmethod
    def _score(findings: List[Dict]) -> float:
        """Return a quality score 0-10 (10 = perfect, 0 = terrible)."""
        penalty = {"critical": 3.0, "high": 2.0, "medium": 1.0, "low": 0.3}
        total   = sum(penalty.get(f.get("severity", "low"), 0.5) for f in findings)
        return max(0.0, round(10.0 - total, 1))

    @staticmethod
    def _is_excluded(path: Path) -> bool:
        excluded = {".git", "__pycache__", ".venv", "venv", "node_modules",
                    "migrations"}
        return any(p in excluded for p in path.parts)
