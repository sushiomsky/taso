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


async def _git(*args, cwd: Path = None) -> Tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr)."""
    cwd = cwd or settings.GIT_REPO_PATH
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"]  = settings.GIT_AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = settings.GIT_AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"]  = settings.GIT_AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = settings.GIT_AUTHOR_EMAIL
    env["GIT_TERMINAL_PROMPT"] = "0"  # Never prompt for credentials

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


async def git_status() -> str:
    rc, out, err = await _git("status", "--short")
    return out or "(clean)"


async def git_current_sha() -> Optional[str]:
    rc, out, _ = await _git("rev-parse", "HEAD")
    return out if rc == 0 else None


async def git_current_branch() -> str:
    rc, out, _ = await _git("rev-parse", "--abbrev-ref", "HEAD")
    return out if rc == 0 else "main"


async def git_init_if_needed() -> None:
    """Initialize git repo if not already initialized."""
    repo = settings.GIT_REPO_PATH
    if not (repo / ".git").exists():
        await _git("init", "-b", "main")
        await _git("config", "user.email", settings.GIT_AUTHOR_EMAIL)
        await _git("config", "user.name", settings.GIT_AUTHOR_NAME)
        log.info("GitManager: initialized new git repository.")


async def git_add_all() -> bool:
    rc, _, err = await _git("add", "-A")
    if rc != 0:
        log.error(f"GitManager: git add failed: {err}")
    return rc == 0


async def git_commit(message: str, version_id: str = None) -> Optional[str]:
    """Stage all changes and commit. Returns new commit SHA or None."""
    await git_add_all()

    # Check if there's anything to commit
    rc, out, _ = await _git("diff", "--cached", "--stat")
    if not out:
        log.info("GitManager: nothing to commit.")
        return await git_current_sha()

    full_msg = f"{message}"
    if version_id:
        full_msg += f"\n\nVersion-Id: {version_id}"

    rc, out, err = await _git("commit", "-m", full_msg)
    if rc != 0:
        log.error(f"GitManager: commit failed: {err}")
        return None

    sha = await git_current_sha()
    log.info(f"GitManager: committed {sha} — {message[:50]}")
    return sha


async def git_tag(tag: str, message: str = "") -> bool:
    rc, _, err = await _git("tag", "-a", tag, "-m", message or tag)
    if rc != 0:
        log.warning(f"GitManager: tag '{tag}' failed: {err}")
    return rc == 0


async def git_push(remote: str = "origin", branch: str = None, tags: bool = True) -> bool:
    """Push to GitHub remote. Returns True on success."""
    if not settings.GITHUB_REPO_URL:
        log.info("GitManager: GITHUB_REPO_URL not set — skipping push.")
        return False

    branch = branch or await git_current_branch()

    # Ensure remote is configured
    rc, remotes, _ = await _git("remote")
    if remote not in remotes.split("\n"):
        await _git("remote", "add", remote, settings.GITHUB_REPO_URL)

    rc, out, err = await _git("push", remote, branch, "--force-with-lease")
    if rc != 0:
        # First push
        rc, out, err = await _git("push", "--set-upstream", remote, branch)

    if rc != 0:
        log.error(f"GitManager: push failed: {err}")
        return False

    if tags:
        await _git("push", remote, "--tags")

    log.info(f"GitManager: pushed to {remote}/{branch}")
    return True


async def git_pull(remote: str = "origin", branch: str = None) -> bool:
    """Pull latest from remote. Returns True on success."""
    if not settings.GITHUB_REPO_URL:
        return False
    branch = branch or await git_current_branch()
    rc, out, err = await _git("pull", remote, branch, "--rebase")
    if rc != 0:
        log.error(f"GitManager: pull failed: {err}")
        return False
    log.info(f"GitManager: pulled from {remote}/{branch}")
    return True


async def git_revert_to(sha: str) -> bool:
    """Hard reset to a specific commit SHA. Returns True on success."""
    rc, _, err = await _git("reset", "--hard", sha)
    if rc != 0:
        log.error(f"GitManager: revert to {sha} failed: {err}")
        return False
    log.info(f"GitManager: reverted to {sha}")
    return True


async def git_log(n: int = 10) -> List[dict]:
    """Return last n commits as list of dicts."""
    fmt = "%H|%s|%an|%ai"
    rc, out, _ = await _git("log", f"-{n}", f"--pretty=format:{fmt}")
    if rc != 0 or not out:
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
