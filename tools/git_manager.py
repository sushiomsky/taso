"""
TASO – Tool: git_manager

Provides Git operations: clone, pull, diff, branch management,
patch application, and commit.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base_tool import BaseTool, ToolSchema
from config.settings import settings


class GitManagerTool(BaseTool):
    name        = "git_manager"
    description = "Manage local Git repositories: clone, pull, diff, apply patches, commit."
    schema      = ToolSchema({
        "action": {
            "type": "str", "required": True,
            "description": "One of: status, log, diff, pull, clone, apply_patch, commit.",
        },
        "repo_path": {
            "type": "str", "required": False,
            "description": "Path to the repository (default: settings.GIT_REPO_PATH).",
        },
        "remote_url": {
            "type": "str", "required": False,
            "description": "Remote URL for clone action.",
        },
        "patch_content": {
            "type": "str", "required": False,
            "description": "Unified diff content for apply_patch action.",
        },
        "commit_message": {
            "type": "str", "required": False,
            "description": "Commit message for commit action.",
        },
        "n_commits": {
            "type": "int", "required": False, "default": 10,
            "description": "Number of log entries to return.",
        },
    })

    async def execute(self, action: str,
                       repo_path: Optional[str] = None,
                       remote_url: Optional[str] = None,
                       patch_content: Optional[str] = None,
                       commit_message: Optional[str] = None,
                       n_commits: int = 10,
                       **_: Any) -> Dict[str, Any]:

        rpath = Path(repo_path) if repo_path else settings.GIT_REPO_PATH

        if action == "status":
            return await self._status(rpath)
        elif action == "log":
            return await self._log(rpath, n_commits)
        elif action == "diff":
            return await self._diff(rpath)
        elif action == "pull":
            return await self._pull(rpath)
        elif action == "clone":
            if not remote_url:
                raise ValueError("remote_url required for clone.")
            return await self._clone(remote_url, rpath)
        elif action == "apply_patch":
            if not patch_content:
                raise ValueError("patch_content required for apply_patch.")
            return await self._apply_patch(rpath, patch_content)
        elif action == "commit":
            if not commit_message:
                raise ValueError("commit_message required for commit.")
            return await self._commit(rpath, commit_message)
        else:
            raise ValueError(f"Unknown git action: {action!r}")

    # ------------------------------------------------------------------

    @staticmethod
    async def _run_git(*args: str, cwd: Path) -> Dict[str, Any]:
        env = {
            "GIT_AUTHOR_NAME":     settings.GIT_AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL":    settings.GIT_AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME":  settings.GIT_AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": settings.GIT_AUTHOR_EMAIL,
        }
        import os
        merged_env = {**os.environ, **env}

        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        return {
            "returncode": proc.returncode,
            "stdout":     stdout.decode(errors="replace").strip(),
            "stderr":     stderr.decode(errors="replace").strip(),
        }

    async def _status(self, path: Path) -> Dict[str, Any]:
        r = await self._run_git("status", "--short", cwd=path)
        return {"status": r["stdout"], "clean": r["stdout"] == ""}

    async def _log(self, path: Path, n: int) -> Dict[str, Any]:
        r = await self._run_git(
            "log", f"-{n}",
            "--pretty=format:%H|%an|%ai|%s",
            cwd=path,
        )
        commits = []
        for line in r["stdout"].splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash":    parts[0][:10],
                    "author":  parts[1],
                    "date":    parts[2],
                    "message": parts[3],
                })
        return {"commits": commits}

    async def _diff(self, path: Path) -> Dict[str, Any]:
        r = await self._run_git("diff", "--stat", "HEAD", cwd=path)
        return {"diff_stat": r["stdout"]}

    async def _pull(self, path: Path) -> Dict[str, Any]:
        r = await self._run_git("pull", "--ff-only", cwd=path)
        return {"output": r["stdout"], "success": r["returncode"] == 0}

    async def _clone(self, url: str, dest: Path) -> Dict[str, Any]:
        r = await self._run_git(
            "clone", "--depth", "1", url, str(dest), cwd=dest.parent
        )
        return {"output": r["stdout"] + r["stderr"],
                "success": r["returncode"] == 0}

    async def _apply_patch(self, path: Path, patch: str) -> Dict[str, Any]:
        """Write patch to a temp file and apply with git apply --check first."""
        import tempfile, os

        with tempfile.NamedTemporaryFile(
            suffix=".patch", mode="w", delete=False
        ) as fh:
            fh.write(patch)
            patch_file = fh.name

        try:
            # Dry-run first
            check = await self._run_git(
                "apply", "--check", patch_file, cwd=path
            )
            if check["returncode"] != 0:
                return {
                    "success": False,
                    "error":   check["stderr"],
                    "dry_run": True,
                }

            # Real apply
            apply = await self._run_git("apply", patch_file, cwd=path)
            return {
                "success": apply["returncode"] == 0,
                "output":  apply["stdout"],
                "error":   apply["stderr"] or None,
            }
        finally:
            os.unlink(patch_file)

    async def _commit(self, path: Path, message: str) -> Dict[str, Any]:
        await self._run_git("add", "-A", cwd=path)
        r = await self._run_git("commit", "-m", message, cwd=path)
        return {
            "success": r["returncode"] == 0,
            "output":  r["stdout"],
            "error":   r["stderr"] or None,
        }
