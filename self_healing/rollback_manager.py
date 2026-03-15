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
    def __init__(self, error_threshold: int = 3) -> None:
        self._error_count = 0
        self._error_threshold = error_threshold
        self._rollback_log: List[dict] = []
        self._last_rollback_time = 0.0
        self._debounce_interval = 300  # seconds

    async def record_error(self, context: str) -> Optional[str]:
        """
        Record an error. If threshold exceeded, trigger rollback.
        Returns rollback SHA if rollback was triggered, else None.
        """
        self._increment_error(context)

        if self._error_count >= self._error_threshold:
            if self._is_debounce_active():
                log.info("RollbackManager: debounce active — skipping rollback.")
                return None
            return await self._trigger_rollback(reason=f"Auto-rollback: {context}")
        return None

    def _increment_error(self, context: str) -> None:
        """Increment error counter (sync, safe to call from tests)."""
        self._error_count += 1
        log.warning(f"RollbackManager: error #{self._error_count} — {context}")

    def reset_errors(self) -> None:
        """Reset the error count to zero."""
        self._error_count = 0

    def reset(self) -> None:
        """Alias for reset_errors — resets error counter after a rollback."""
        self._error_count = 0

    def should_rollback(self) -> bool:
        """Return True if the error threshold has been reached."""
        return self._error_count >= self._error_threshold

    async def rollback(self, reason: str = "manual rollback",
                       target_sha: Optional[str] = None) -> Optional[str]:
        """
        Revert to target SHA or last stable version.
        Returns the SHA rolled back to, or None on failure.
        """
        target_sha = target_sha or self._get_last_stable_sha()
        if not target_sha:
            log.error("RollbackManager: no stable version to roll back to.")
            return None

        return await self._trigger_rollback(reason=reason, target_sha=target_sha)

    def rollback_history(self) -> List[dict]:
        """
        Retrieve the last 10 rollback attempts.
        """
        return self._rollback_log[-10:]

    def _is_debounce_active(self) -> bool:
        """
        Check if rollback debounce is active based on the last rollback time.
        """
        time_since_last_rollback = time.time() - self._last_rollback_time
        is_active = time_since_last_rollback < self._debounce_interval
        if is_active:
            log.debug(f"RollbackManager: debounce active, {self._debounce_interval - time_since_last_rollback:.2f}s remaining.")
        return is_active

    def _get_last_stable_sha(self) -> Optional[str]:
        """
        Retrieve the last stable commit SHA from the version manager.
        """
        try:
            last_stable = version_manager.last_stable()
            if not last_stable or not last_stable.commit_sha:
                log.error("RollbackManager: no stable version available.")
                return None
            return last_stable.commit_sha
        except Exception as e:
            log.error(f"RollbackManager: failed to retrieve last stable SHA due to exception: {e}")
            return None

    async def _trigger_rollback(self, reason: str, target_sha: str) -> Optional[str]:
        """
        Perform the rollback operation and log the result.
        """
        log.warning(f"RollbackManager: rolling back to {target_sha} — reason: {reason}")
        try:
            success = await git_revert_to(target_sha)
        except Exception as e:
            log.error(f"RollbackManager: rollback to {target_sha} FAILED due to exception: {e}")
            success = False

        self._record_rollback(target_sha, reason, success)
        return target_sha if success else None

    def _record_rollback(self, target_sha: str, reason: str, success: bool) -> None:
        """
        Record the details of a rollback attempt.
        """
        timestamp = time.time()
        entry = {
            "time": timestamp,
            "reason": reason,
            "target_sha": target_sha,
            "success": success,
        }
        self._rollback_log.append(entry)
        self._last_rollback_time = timestamp
        self._error_count = 0

        if success:
            log.info(f"RollbackManager: rollback to {target_sha} succeeded at {time.ctime(timestamp)}.")
        else:
            log.error(f"RollbackManager: rollback to {target_sha} FAILED at {time.ctime(timestamp)}.")


rollback_manager = RollbackManager()
