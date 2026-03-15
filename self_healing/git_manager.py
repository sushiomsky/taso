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


# ---------------------------------------------------------------------------
# Branch management
# ---------------------------------------------------------------------------

async def git_fetch(remote: str = "origin") -> bool:
    """Fetch from remote (no merge). Returns True on success."""
    if not settings.GITHUB_REPO_URL:
        log.info("GitManager: No remote configured — skipping fetch.")
        return True
    rc, _, err = await _git("fetch", remote)
    if rc != 0:
        log.warning(f"GitManager: fetch failed: {err}")
    return rc == 0


async def git_create_branch(branch: str) -> bool:
    """Create and checkout a new branch. Returns True on success."""
    rc, _, err = await _git("checkout", "-b", branch)
    if rc != 0:
        log.error(f"GitManager: Failed to create branch '{branch}': {err}")
    return rc == 0


async def git_checkout(branch: str) -> bool:
    """Checkout an existing branch. Returns True on success."""
    rc, _, err = await _git("checkout", branch)
    if rc != 0:
        log.error(f"GitManager: Failed to checkout '{branch}': {err}")
    return rc == 0


async def git_merge(source_branch: str, target_branch: str = "main",
                    no_ff: bool = True) -> bool:
    """
    Merge source_branch into target_branch.
    Checks out target first, then merges.
    Returns True on success.
    """
    if not await git_checkout(target_branch):
        return False
    flags = ["--no-ff"] if no_ff else []
    rc, _, err = await _git("merge", *flags, source_branch,
                             "-m", f"Merge {source_branch} into {target_branch}")
    if rc != 0:
        log.error(f"GitManager: Merge '{source_branch}' → '{target_branch}' failed: {err}")
    return rc == 0


async def git_delete_branch(branch: str, force: bool = False) -> bool:
    """Delete a local branch. Returns True on success."""
    flag = "-D" if force else "-d"
    rc, _, err = await _git("branch", flag, branch)
    if rc != 0:
        log.warning(f"GitManager: Failed to delete branch '{branch}': {err}")
    return rc == 0


async def git_list_branches() -> List[str]:
    """Return a list of local branch names."""
    rc, out, err = await _git("branch", "--list", "--format=%(refname:short)")
    if rc != 0:
        log.error(f"GitManager: Failed to list branches: {err}")
        return []
    return [b.strip() for b in out.split("\n") if b.strip()]


async def git_diff_stats() -> dict:
    """
    Return stats on uncommitted changes.
    Returns: {files_changed: int, insertions: int, deletions: int, files: List[str]}
    """
    # List of changed files
    rc, out, _ = await _git("diff", "--name-only", "HEAD")
    changed_files = [f.strip() for f in out.split("\n") if f.strip()] if rc == 0 else []

    # Staged + unstaged count
    rc2, out2, _ = await _git("diff", "--stat", "HEAD")
    insertions = 0
    deletions  = 0
    if rc2 == 0 and out2:
        for line in out2.split("\n"):
            import re
            m = re.search(r"(\d+) insertion", line)
            if m:
                insertions += int(m.group(1))
            m = re.search(r"(\d+) deletion", line)
            if m:
                deletions += int(m.group(1))

    return {
        "files_changed": len(changed_files),
        "insertions":    insertions,
        "deletions":     deletions,
        "files":         changed_files,
    }


async def git_stash() -> bool:
    """Stash all uncommitted changes. Returns True on success."""
    rc, _, err = await _git("stash", "push", "-m", "auto-stash by TASO")
    if rc != 0:
        log.warning(f"GitManager: stash failed: {err}")
    return rc == 0


async def git_stash_pop() -> bool:
    """Pop the most recent stash. Returns True on success."""
    rc, _, err = await _git("stash", "pop")
    if rc != 0:
        log.warning(f"GitManager: stash pop failed: {err}")
    return rc == 0


async def git_sync_main() -> dict:
    """
    Full repository synchronisation:
      1. git fetch origin
      2. git checkout main
      3. git pull origin main
      4. Return summary of changes (log since previous HEAD)

    Returns dict with: {success, previous_sha, current_sha, new_commits}
    """
    previous_sha = await git_current_sha() or "unknown"

    fetch_ok  = await git_fetch()
    checkout_ok = await git_checkout("main")
    pull_ok   = await git_pull("origin", "main")

    current_sha = await git_current_sha() or "unknown"

    # Collect new commits since last sync
    new_commits: List[dict] = []
    if previous_sha != current_sha and previous_sha != "unknown":
        rc, out, _ = await _git(
            "log", f"{previous_sha}..HEAD",
            "--pretty=format:%H|%s|%an|%ai"
        )
        if rc == 0 and out:
            for line in out.split("\n"):
                parts = line.split("|", 3)
                if len(parts) == 4:
                    new_commits.append({
                        "sha": parts[0][:12],
                        "message": parts[1],
                        "author": parts[2],
                        "date": parts[3],
                    })

    success = fetch_ok and checkout_ok and pull_ok
    log.info(
        f"GitManager: sync_main {'succeeded' if success else 'partial'} — "
        f"{len(new_commits)} new commit(s) since {previous_sha[:8]}"
    )
    return {
        "success":      success,
        "fetch_ok":     fetch_ok,
        "checkout_ok":  checkout_ok,
        "pull_ok":      pull_ok,
        "previous_sha": previous_sha,
        "current_sha":  current_sha,
        "new_commits":  new_commits,
    }


async def git_create_pr(
    source_branch: str,
    title: str,
    body: str = "",
    base: str = "main",
) -> Optional[str]:
    """
    Create a GitHub pull request via the REST API.
    Requires GITHUB_REPO_URL and GITHUB_TOKEN to be set.
    Returns the PR URL on success, None on failure.
    """
    import re as _re, json as _json

    repo_url = settings.GITHUB_REPO_URL
    token    = settings.GITHUB_TOKEN
    if not repo_url or not token:
        log.info("GitManager: GitHub credentials not set — cannot create PR.")
        return None

    # Extract owner/repo from URL
    m = _re.search(r"github\.com[:/](.+?)(?:\.git)?$", repo_url)
    if not m:
        log.error(f"GitManager: Cannot parse repo from URL: {repo_url}")
        return None
    owner_repo = m.group(1)  # e.g. "sushiomsky/taso"

    import aiohttp
    api_url = f"https://api.github.com/repos/{owner_repo}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {
        "title": title,
        "body":  body,
        "head":  source_branch,
        "base":  base,
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.post(api_url, json=payload,
                                  timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
                if resp.status in (200, 201):
                    pr_url = data.get("html_url", "")
                    log.info(f"GitManager: PR created: {pr_url}")
                    return pr_url
                else:
                    log.error(f"GitManager: PR creation failed {resp.status}: {data.get('message', '')}")
                    return None
    except Exception as exc:
        log.error(f"GitManager: PR API error: {exc}")
        return None
