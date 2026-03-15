"""
TASO – Self-Healing Git Manager

Async Git operations: commit, tag, push, pull, revert.
Designed for automated agent-driven version control.
"""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import List, Optional, Tuple

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("git_manager")


async def _git(*args, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr)."""
    cwd = cwd or settings.GIT_REPO_PATH
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": settings.GIT_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": settings.GIT_AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": settings.GIT_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": settings.GIT_AUTHOR_EMAIL,
        "GIT_TERMINAL_PROMPT": "0",  # Never prompt for credentials
    })

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )
    except Exception as e:
        log.error(f"GitManager: Error running git command {' '.join(args)}: {e}")
        return 1, "", str(e)


async def git_status() -> str:
    rc, out, err = await _git("status", "--short")
    if rc != 0:
        log.error(f"GitManager: Failed to get git status: {err}")
        return "(error retrieving status)"
    return out or "(clean)"


async def git_current_sha() -> Optional[str]:
    rc, out, err = await _git("rev-parse", "HEAD")
    if rc != 0:
        log.error(f"GitManager: Failed to get current SHA: {err}")
        return None
    return out


async def git_current_branch() -> str:
    rc, out, err = await _git("rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        log.error(f"GitManager: Failed to get current branch: {err}")
        return "main"
    return out


async def git_init_if_needed() -> None:
    """Initialize git repo if not already initialized."""
    repo = settings.GIT_REPO_PATH
    if not (repo / ".git").exists():
        try:
            await _git("init", "-b", "main")
            await _git("config", "user.email", settings.GIT_AUTHOR_EMAIL)
            await _git("config", "user.name", settings.GIT_AUTHOR_NAME)
            log.info("GitManager: Initialized new git repository.")
        except Exception as e:
            log.error(f"GitManager: Failed to initialize git repository: {e}")


async def git_add_all() -> bool:
    rc, _, err = await _git("add", "-A")
    if rc != 0:
        log.error(f"GitManager: git add failed: {err}")
    return rc == 0


async def git_commit(message: str, version_id: Optional[str] = None) -> Optional[str]:
    """Stage all changes and commit. Returns new commit SHA or None."""
    if not await git_add_all():
        return None

    # Check if there's anything to commit
    rc, out, err = await _git("diff", "--cached", "--stat")
    if rc != 0:
        log.error(f"GitManager: Failed to check changes to commit: {err}")
        return None
    if not out:
        log.info("GitManager: Nothing to commit.")
        return await git_current_sha()

    full_msg = message
    if version_id:
        full_msg += f"\n\nVersion-Id: {version_id}"

    rc, _, err = await _git("commit", "-m", full_msg)
    if rc != 0:
        log.error(f"GitManager: Commit failed: {err}")
        return None

    sha = await git_current_sha()
    if sha:
        log.info(f"GitManager: Committed {sha} — {message[:50]}")
    return sha


async def git_tag(tag: str, message: str = "") -> bool:
    rc, _, err = await _git("tag", "-a", tag, "-m", message or tag)
    if rc != 0:
        log.warning(f"GitManager: Tag '{tag}' failed: {err}")
    return rc == 0


async def git_push(remote: str = "origin", branch: Optional[str] = None, tags: bool = True) -> bool:
    """Push to GitHub remote. Returns True on success."""
    if not settings.GITHUB_REPO_URL:
        log.info("GitManager: GITHUB_REPO_URL not set — skipping push.")
        return False

    branch = branch or await git_current_branch()

    # Ensure remote is configured
    rc, remotes, err = await _git("remote")
    if rc != 0:
        log.error(f"GitManager: Failed to list remotes: {err}")
        return False
    if remote not in remotes.split("\n"):
        rc, _, err = await _git("remote", "add", remote, settings.GITHUB_REPO_URL)
        if rc != 0:
            log.error(f"GitManager: Failed to add remote '{remote}': {err}")
            return False

    rc, _, err = await _git("push", remote, branch, "--force-with-lease")
    if rc != 0:
        # First push
        rc, _, err = await _git("push", "--set-upstream", remote, branch)
        if rc != 0:
            log.error(f"GitManager: Push failed: {err}")
            return False

    if tags:
        rc, _, err = await _git("push", remote, "--tags")
        if rc != 0:
            log.error(f"GitManager: Failed to push tags: {err}")
            return False

    log.info(f"GitManager: Pushed to {remote}/{branch}")
    return True


async def git_pull(remote: str = "origin", branch: Optional[str] = None) -> bool:
    """Pull latest from remote. Returns True on success."""
    if not settings.GITHUB_REPO_URL:
        log.info("GitManager: GITHUB_REPO_URL not set — skipping pull.")
        return False

    branch = branch or await git_current_branch()
    rc, _, err = await _git("pull", remote, branch, "--rebase")
    if rc != 0:
        log.error(f"GitManager: Pull failed: {err}")
        return False

    log.info(f"GitManager: Pulled from {remote}/{branch}")
    return True


async def git_revert_to(sha: str) -> bool:
    """Hard reset to a specific commit SHA. Returns True on success."""
    rc, _, err = await _git("reset", "--hard", sha)
    if rc != 0:
        log.error(f"GitManager: Revert to {sha} failed: {err}")
        return False
    log.info(f"GitManager: Reverted to {sha}")
    return True


async def git_log(n: int = 10) -> List[dict]:
    """Return last n commits as list of dicts."""
    fmt = "%H|%s|%an|%ai"
    rc, out, err = await _git("log", f"-{n}", f"--pretty=format:{fmt}")
    if rc != 0:
        log.error(f"GitManager: Failed to retrieve git log: {err}")
        return []
    if not out:
        log.info("GitManager: No commits found in git log.")
        return []

    results = []
    for line in out.split("\n"):
        parts = line.split("|", 3)
        if len(parts) == 4:
            results.append({
                "sha": parts[0][:12],
                "message": parts[1],
                "author": parts[2],
                "date": parts[3],
            })
    return results
