"""
TASO – Self-Healing Agent

Coordinates the full commit-test-push-deploy pipeline.
Monitors for runtime errors and triggers rollback if needed.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from config.settings import settings

log = get_logger("agent")


class SelfHealingAgent(BaseAgent):
    name        = "self_healing"
    description = "Commits, pushes, and monitors code health. Triggers rollback on failure."

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("self_healing.*", self._handle_request)
        self._bus.subscribe("error.critical",  self._handle_critical_error)

    async def _handle_request(self, msg: BusMessage) -> None:
        action = msg.payload.get("action", "")

        if action == "commit_and_push":
            result = await self._commit_push(
                message=msg.payload.get("message", "TASO auto-commit"),
                version_id=msg.payload.get("version_id", ""),
            )
        elif action == "rollback":
            result = await self._rollback(
                reason=msg.payload.get("reason", "manual rollback"),
                target_sha=msg.payload.get("sha"),
            )
        elif action == "deploy":
            result = await self._deploy()
        elif action == "status":
            result = self._status()
        else:
            result = f"Unknown action: {action}"

        if msg.reply_to:
            await self.publish(
                topic=msg.reply_to,
                payload={"result": result, "agent": self.name},
                recipient=msg.sender,
            )

    async def _handle_critical_error(self, msg: BusMessage) -> None:
        context = msg.payload.get("context", "unknown error")
        from self_healing.rollback_manager import rollback_manager
        sha = await rollback_manager.record_error(context)
        if sha:
            await _version_history_db_log_rollback(context, sha)

    async def _commit_push(self, message: str, version_id: str) -> str:
        from self_healing.deploy_manager import deploy_manager
        from self_healing.version_manager import version_manager
        from memory.version_history_db import version_history_db

        sha = await deploy_manager.commit_and_push(
            message=message,
            version_id=version_id,
            author_agent=self.name,
        )
        if sha:
            version_manager.mark_stable(version_id, sha)
            rec = version_manager.get(version_id)
            if rec:
                await version_history_db.log_version(rec)
            return f"✅ Committed & pushed: {sha[:12]} — {message[:50]}"
        return "❌ Commit/push failed — check logs."

    async def _rollback(self, reason: str, target_sha: str = None) -> str:
        from self_healing.rollback_manager import rollback_manager
        from self_healing.git_manager import git_current_sha
        from memory.version_history_db import version_history_db

        current = await git_current_sha()
        sha = await rollback_manager.rollback(reason=reason, target_sha=target_sha)
        if sha:
            await version_history_db.log_rollback(reason, current or "?", sha, True, self.name)
            return f"✅ Rolled back to {sha[:12]} — reason: {reason}"
        return "❌ Rollback failed — no stable version found."

    async def _deploy(self) -> str:
        from self_healing.deploy_manager import deploy_manager
        ok = await deploy_manager.bootstrap()
        sha = deploy_manager.current_sha
        return f"{'✅' if ok else '⚠️'} Deploy complete. SHA: {sha[:12] if sha else 'unknown'}"

    def _status(self) -> str:
        from self_healing.version_manager import version_manager
        from self_healing.rollback_manager import rollback_manager
        s = version_manager.status_dict()
        rb = rollback_manager.rollback_history()
        return (
            f"Versions: {s['total_versions']} total, {s['stable_versions']} stable\n"
            f"Last stable: {s['last_stable']}\n"
            f"Rollbacks: {len(rb)}"
        )

    async def handle(self, description: str, context: str = "") -> str:
        return self._status()


async def _version_history_db_log_rollback(reason: str, sha: str) -> None:
    try:
        from memory.version_history_db import version_history_db
        await version_history_db.log_rollback(reason, "?", sha, True, "self_healing_agent")
    except Exception:
        pass
