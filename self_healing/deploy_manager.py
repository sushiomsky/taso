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
from self_healing.test_runner import run_smoke_test, run_syntax_check, run_pytest
from self_healing.rollback_manager import rollback_manager
from self_healing.version_tagger import version_tagger

log = get_logger("deploy_manager")


class DeployManager:
    def __init__(self) -> None:
        self._deployed_sha: Optional[str] = None
        self._deploy_log: list[str] = []

    async def bootstrap(self) -> bool:
        """
        Called at startup. Pulls latest from GitHub if configured,
        runs health checks + smoke tests, and marks deployment.
        Falls back to last stable tag if health checks fail.
        Returns True if deployment is good.
        """
        try:
            if not settings.AUTO_DEPLOY_ON_START:
                log.info("AUTO_DEPLOY_ON_START is false — skipping bootstrap pull.")
                self._deployed_sha = await self._safe_get_current_sha()
                return True

            if not settings.GITHUB_REPO_URL:
                log.info("No GITHUB_REPO_URL configured — skipping pull.")
                self._deployed_sha = await self._safe_get_current_sha()
                return True

            log.info("Pulling latest from GitHub...")
            if not await self._safe_git_pull():
                log.warning("Git pull failed — using local version.")
                self._deployed_sha = await self._safe_get_current_sha()
                return True  # Continue with the local version

            # Run health checks; if critical failure → rollback to last stable tag
            try:
                from self_healing.health_checker import health_checker
                report = await health_checker.check_all(quick=True)
                if not report.passed:
                    log.error(
                        f"Health checks failed after pull: {report.failed_checks}. "
                        "Checking out last stable tag…"
                    )
                    last_tag = await version_tagger.get_latest_tag()
                    if last_tag:
                        from self_healing.git_manager import git_checkout
                        await git_checkout(last_tag)
                        log.info(f"Rolled back to {last_tag}")
                    self._deployed_sha = await self._safe_get_current_sha()
                    return False
            except Exception as hc_exc:
                log.warning(f"Health checker unavailable during bootstrap: {hc_exc}")

            if not await self._run_smoke_tests("after pull"):
                await rollback_manager.rollback(reason="Failed smoke test after git pull")
                self._deployed_sha = await self._safe_get_current_sha()
                return False

            self._deployed_sha = await self._safe_get_current_sha()
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
                log.error("Tests failed. Aborting commit and push.")
                return None

            sha = await self._safe_git_commit(message, version_id)
            if not sha:
                log.error("Git commit failed.")
                return None

            if not await self._safe_git_tag(version_id, f"{author_agent}: {message[:60]}"):
                log.error("Git tag failed.")
                return None

            if not await self._safe_git_push():
                log.error("Git push failed.")
                return None

            self._deployed_sha = sha
            log.info(f"Deployed {version_id} (SHA: {sha})")
            return sha
        except Exception as e:
            log.exception(f"Unexpected error during commit and push: {e}")
            return None

    async def _run_all_tests(self) -> bool:
        """
        Runs syntax, pytest, runtime health checks, and smoke tests.
        Returns True only if all gates pass.
        """
        try:
            passed, output = await run_syntax_check()
            if not passed:
                log.error(f"Syntax check failed:\n{output}")
                return False

            passed, output = await run_pytest()
            if not passed:
                log.error(f"Pytest failed:\n{output[-1000:]}")
                return False

            from self_healing.health_checker import health_checker
            report = await health_checker.check_all(quick=True)
            if not report.passed:
                log.error(f"Health checks failed before commit: {report.failed_checks}")
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

    async def _safe_get_current_sha(self) -> Optional[str]:
        """
        Safely retrieves the current git SHA.
        """
        try:
            return await git_current_sha()
        except Exception as e:
            log.exception(f"Failed to retrieve current git SHA: {e}")
            return None

    async def _safe_git_pull(self) -> bool:
        """
        Safely performs a git pull operation.
        Returns True if successful, False otherwise.
        """
        try:
            return await git_pull()
        except Exception as e:
            log.exception(f"Git pull failed: {e}")
            return False

    async def _safe_git_commit(self, message: str, version_id: str) -> Optional[str]:
        """
        Safely performs a git commit operation.
        Returns the commit SHA if successful, None otherwise.
        """
        try:
            return await git_commit(message, version_id)
        except Exception as e:
            log.exception(f"Git commit failed: {e}")
            return None

    async def _safe_git_tag(self, version_id: str, tag_message: str) -> bool:
        """
        Safely performs a git tag operation.
        Returns True if successful, False otherwise.
        """
        try:
            return await git_tag(version_id, tag_message)
        except Exception as e:
            log.exception(f"Git tag failed: {e}")
            return False

    async def _safe_git_push(self) -> bool:
        """
        Safely performs a git push operation.
        Returns True if successful, False otherwise.
        """
        try:
            return await git_push()
        except Exception as e:
            log.exception(f"Git push failed: {e}")
            return False

    @property
    def current_sha(self) -> Optional[str]:
        return self._deployed_sha


deploy_manager = DeployManager()
