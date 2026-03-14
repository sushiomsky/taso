"""
TASO – Rollback Manager

Monitors system health and triggers rollback to last stable commit
when errors exceed thresholds.
"""
from __future__ import annotations
import asyncio
import time
from typing import List, Optional

from config.logging_config import get_logger
from self_healing.git_manager import git_revert_to, git_current_sha
from self_healing.version_manager import version_manager, VersionRecord

log = get_logger("rollback_manager")


class RollbackManager:
    def __init__(self) -> None:
        self._error_count = 0
        self._error_threshold = 3
        self._rollback_log: List[dict] = []
        self._last_rollback_time = 0.0

    async def record_error(self, context: str) -> Optional[str]:
        """
        Record an error. If threshold exceeded, trigger rollback.
        Returns rollback SHA if rollback was triggered, else None.
        """
        self._error_count += 1
        log.warning(f"RollbackManager: error #{self._error_count} — {context}")

        if self._error_count >= self._error_threshold:
            # Debounce: don't rollback more than once per 5 minutes
            if time.time() - self._last_rollback_time < 300:
                log.info("RollbackManager: debounce — skipping rollback.")
                return None
            return await self.rollback(reason=f"Auto-rollback: {context}")
        return None

    def reset_errors(self) -> None:
        self._error_count = 0

    async def rollback(self, reason: str = "manual rollback",
                       target_sha: str = None) -> Optional[str]:
        """
        Revert to target SHA or last stable version.
        Returns the SHA rolled back to, or None on failure.
        """
        if target_sha is None:
            last_stable = version_manager.last_stable()
            if not last_stable or not last_stable.commit_sha:
                log.error("RollbackManager: no stable version to roll back to.")
                return None
            target_sha = last_stable.commit_sha

        log.warning(f"RollbackManager: rolling back to {target_sha} — reason: {reason}")
        success = await git_revert_to(target_sha)

        entry = {
            "time": time.time(),
            "reason": reason,
            "target_sha": target_sha,
            "success": success,
        }
        self._rollback_log.append(entry)
        self._last_rollback_time = time.time()
        self._error_count = 0

        if success:
            log.info(f"RollbackManager: rollback to {target_sha} succeeded.")
        else:
            log.error(f"RollbackManager: rollback to {target_sha} FAILED.")
        return target_sha if success else None

    def rollback_history(self) -> List[dict]:
        return self._rollback_log[-10:]


rollback_manager = RollbackManager()
