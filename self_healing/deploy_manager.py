"""
TASO – Deploy Manager

Handles:
  1. Bootstrap on startup (pull latest from GitHub, test, deploy)
  2. Hot-reload after a patch is applied
"""
from __future__ import annotations
import asyncio
import os
import sys
from typing import Optional

from config.logging_config import get_logger
from config.settings import settings
from self_healing.git_manager import git_pull, git_current_sha
from self_healing.test_runner import run_smoke_test, run_syntax_check

log = get_logger("deploy_manager")


class DeployManager:
    def __init__(self) -> None:
        self._deployed_sha: Optional[str] = None
        self._deploy_log: list = []

    async def bootstrap(self) -> bool:
        """
        Called at startup. Pulls latest from GitHub if configured,
        runs smoke tests, and marks deployment.
        Returns True if deployment is good.
        """
        if not settings.AUTO_DEPLOY_ON_START:
            log.info("DeployManager: AUTO_DEPLOY_ON_START is false — skipping bootstrap pull.")
            self._deployed_sha = await git_current_sha()
            return True

        if not settings.GITHUB_REPO_URL:
            log.info("DeployManager: no GITHUB_REPO_URL — skipping pull.")
            self._deployed_sha = await git_current_sha()
            return True

        log.info("DeployManager: pulling latest from GitHub…")
        pulled = await git_pull()
        if not pulled:
            log.warning("DeployManager: pull failed — using local version.")
            self._deployed_sha = await git_current_sha()
            return True  # still continue with local

        # Run smoke tests
        passed, output = await run_smoke_test()
        if not passed:
            log.error(f"DeployManager: smoke test failed after pull:\n{output}")
            # Rollback pull
            from self_healing.rollback_manager import rollback_manager
            await rollback_manager.rollback(reason="Failed smoke test after git pull")
            self._deployed_sha = await git_current_sha()
            return False

        self._deployed_sha = await git_current_sha()
        log.info(f"DeployManager: deployed SHA {self._deployed_sha}")
        return True

    async def commit_and_push(
        self,
        message: str,
        version_id: str,
        author_agent: str = "self_healing",
        run_tests: bool = True,
    ) -> Optional[str]:
        """
        Full pipeline: test → commit → tag → push.
        Returns commit SHA on success, None on failure.
        """
        if run_tests:
            passed, output = await run_syntax_check()
            if not passed:
                log.error(f"DeployManager: syntax check failed:\n{output}")
                return None

            passed, output = await run_smoke_test()
            if not passed:
                log.error(f"DeployManager: smoke test failed:\n{output}")
                return None

        from self_healing.git_manager import git_commit, git_tag, git_push
        sha = await git_commit(message, version_id)
        if not sha:
            return None

        await git_tag(version_id, f"{author_agent}: {message[:60]}")
        await git_push()

        self._deployed_sha = sha
        log.info(f"DeployManager: deployed {version_id} ({sha})")
        return sha

    @property
    def current_sha(self) -> Optional[str]:
        return self._deployed_sha


deploy_manager = DeployManager()
