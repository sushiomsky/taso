"""
TASO – Tool: repo_analyzer

Analyses a Git repository: counts lines of code, language breakdown,
file tree summary, recent commit history, and open TODO/FIXME comments.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from tools.base_tool import BaseTool, ToolSchema


class RepoAnalyzerTool(BaseTool):
    name        = "repo_analyzer"
    description = "Analyse a local git repository (LOC, languages, commits, TODOs)."
    schema      = ToolSchema({
        "repo_path": {"type": "str", "required": True,
                      "description": "Absolute path to the repository root."},
        "max_files": {"type": "int", "required": False, "default": 500,
                      "description": "Maximum files to scan."},
    })

    async def execute(self, repo_path: str, max_files: int = 500, **_: Any) -> Dict[str, Any]:
        path = Path(repo_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Repository not found: {repo_path}")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._analyse_sync, path, max_files
        )

    # ------------------------------------------------------------------
    # Sync implementation (runs in thread pool)
    # ------------------------------------------------------------------

    def _analyse_sync(self, path: Path, max_files: int) -> Dict[str, Any]:
        languages: Dict[str, int]  = defaultdict(int)
        total_loc   = 0
        file_count  = 0
        todos: List[Dict]  = []
        file_sizes: List[int] = []

        ext_map = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".go": "Go",      ".rs": "Rust",       ".c": "C",
            ".cpp": "C++",    ".java": "Java",      ".rb": "Ruby",
            ".sh": "Shell",   ".yaml": "YAML",      ".yml": "YAML",
            ".json": "JSON",  ".md": "Markdown",    ".html": "HTML",
            ".css": "CSS",    ".sql": "SQL",
        }

        for fpath in sorted(path.rglob("*"))[:max_files]:
            if not fpath.is_file():
                continue
            if any(p in fpath.parts for p in (".git", "__pycache__", "node_modules", ".venv")):
                continue

            ext  = fpath.suffix.lower()
            lang = ext_map.get(ext, "Other")
            file_count += 1
            file_sizes.append(fpath.stat().st_size)

            try:
                lines = fpath.read_text(errors="ignore").splitlines()
                loc   = len(lines)
                total_loc += loc
                languages[lang] += loc

                # Scan for TODO / FIXME / HACK / XXX
                for i, line in enumerate(lines, 1):
                    upper = line.upper()
                    for marker in ("TODO", "FIXME", "HACK", "XXX"):
                        if marker in upper:
                            todos.append({
                                "file":   str(fpath.relative_to(path)),
                                "line":   i,
                                "marker": marker,
                                "text":   line.strip()[:100],
                            })
                            break
            except Exception:
                pass

        # Recent git commits
        commits = self._git_log(path)

        # Dependency files
        dep_files = [
            str(f.relative_to(path))
            for f in [
                path / "requirements.txt",
                path / "pyproject.toml",
                path / "package.json",
                path / "go.mod",
                path / "Cargo.toml",
            ]
            if f.exists()
        ]

        avg_size = int(sum(file_sizes) / len(file_sizes)) if file_sizes else 0

        return {
            "path":        str(path),
            "file_count":  file_count,
            "total_loc":   total_loc,
            "avg_file_bytes": avg_size,
            "languages":   dict(sorted(languages.items(), key=lambda x: -x[1])),
            "todos":       todos[:50],
            "recent_commits": commits,
            "dependency_files": dep_files,
        }

    @staticmethod
    def _git_log(path: Path, n: int = 10) -> List[Dict]:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "log",
                 f"--max-count={n}",
                 "--pretty=format:%H|%an|%ae|%ai|%s"],
                capture_output=True, text=True, timeout=10,
            )
            commits = []
            for line in result.stdout.strip().splitlines():
                parts = line.split("|", 4)
                if len(parts) == 5:
                    commits.append({
                        "hash":    parts[0][:10],
                        "author":  parts[1],
                        "email":   parts[2],
                        "date":    parts[3],
                        "message": parts[4],
                    })
            return commits
        except Exception:
            return []
