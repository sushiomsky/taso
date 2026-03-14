"""
TASO – Deploy Manager

Handles:
  1. Bootstrap on startup (pull latest from GitHub, test, deploy)
  2. Hot-reload after a patch is applied
"""
from __future__ import annotations
import asyncio
from typing import Optional

from config.logging_config import get_logger
from config.settings import settings
from self_healing.git_manager import git_pull, git_current_sha, git_commit, git_tag, git_push
from self_healing.test_runner import run_smoke_test, run_syntax_check
from self_healing.rollback_manager import rollback_manager

log = get_logger("deploy_manager")


class DeployManager:
    def __init__(self) -> None:
        self._deployed_sha: Optional[str] = None
        self._deploy_log: list[str] = []

    async def bootstrap(self) -> bool:
        """
        Called at startup. Pulls latest from GitHub if configured,
        runs smoke tests, and marks deployment.
        Returns True if deployment is good.
        """
        try:
            if not settings.AUTO_DEPLOY_ON_START:
                log.info("AUTO_DEPLOY_ON_START is false — skipping bootstrap pull.")
                self._deployed_sha = await self._get_current_sha()
                return True

            if not settings.GITHUB_REPO_URL:
                log.info("No GITHUB_REPO_URL configured — skipping pull.")
                self._deployed_sha = await self._get_current_sha()
                return True

            log.info("Pulling latest from GitHub...")
            if not await git_pull():
                log.warning("Git pull failed — using local version.")
                self._deployed_sha = await self._get_current_sha()
                return True  # Continue with the local version

            if not await self._run_smoke_tests("after pull"):
                await rollback_manager.rollback(reason="Failed smoke test after git pull")
                self._deployed_sha = await self._get_current_sha()
                return False

            self._deployed_sha = await self._get_current_sha()
            log.info(f"Deployed SHA: {self._deployed_sha}")
            return True
        except Exception as e:
            log.exception(f"Unexpected error during bootstrap: {e}")
            return False

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
        try:
            if run_tests and not await self._run_all_tests():
                return None

            sha = await git_commit(message, version_id)
            if not sha:
                log.error("Git commit failed.")
                return None

            await git_tag(version_id, f"{author_agent}: {message[:60]}")
            await git_push()

            self._deployed_sha = sha
            log.info(f"Deployed {version_id} (SHA: {sha})")
            return sha
        except Exception as e:
            log.exception(f"Unexpected error during commit and push: {e}")
            return None

    async def _run_all_tests(self) -> bool:
        """
        Runs syntax and smoke tests. Returns True if all tests pass.
        """
        try:
            passed, output = await run_syntax_check()
            if not passed:
                log.error(f"Syntax check failed:\n{output}")
                return False

            return await self._run_smoke_tests("before commit")
        except Exception as e:
            log.exception(f"Unexpected error during tests: {e}")
            return False

    async def _run_smoke_tests(self, context: str) -> bool:
        """
        Runs smoke tests. Returns True if tests pass.
        """
        try:
            passed, output = await run_smoke_test()
            if not passed:
                log.error(f"Smoke test failed {context}:\n{output}")
                return False
            return True
        except Exception as e:
            log.exception(f"Unexpected error during smoke tests {context}: {e}")
            return False

    async def _get_current_sha(self) -> Optional[str]:
        """
        Safely retrieves the current git SHA.
        """
        try:
            return await git_current_sha()
        except Exception as e:
            log.exception(f"Failed to retrieve current git SHA: {e}")
            return None

    @property
    def current_sha(self) -> Optional[str]:
        return self._deployed_sha


deploy_manager = DeployManager()
