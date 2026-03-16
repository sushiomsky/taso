"""
SelfHealingAgent regression tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_commit_push_success_writes_audit():
    from agents.self_healing_agent import SelfHealingAgent

    agent = SelfHealingAgent.__new__(SelfHealingAgent)

    with patch("self_healing.deploy_manager.deploy_manager") as dm, \
         patch("self_healing.version_manager.version_manager") as vm, \
         patch("memory.version_history_db.version_history_db") as vh, \
         patch("memory.audit_log.audit_log") as al:
        dm.commit_and_push = AsyncMock(return_value="abcdef1234567890")
        vm.mark_stable = MagicMock()
        vm.get = MagicMock(return_value=None)
        vh.log_version = AsyncMock()
        al.connect = AsyncMock()
        al.record = AsyncMock()

        result = await agent._commit_push("test message", "v1")

    assert "Committed & pushed" in result
    al.record.assert_called_once()
    kwargs = al.record.call_args.kwargs
    assert kwargs["success"] is True
    assert kwargs["action"] == "commit_and_push"


@pytest.mark.asyncio
async def test_commit_push_failure_writes_audit():
    from agents.self_healing_agent import SelfHealingAgent

    agent = SelfHealingAgent.__new__(SelfHealingAgent)

    with patch("self_healing.deploy_manager.deploy_manager") as dm, \
         patch("self_healing.version_manager.version_manager") as vm, \
         patch("memory.version_history_db.version_history_db") as vh, \
         patch("memory.audit_log.audit_log") as al:
        dm.commit_and_push = AsyncMock(return_value=None)
        vm.mark_stable = MagicMock()
        vm.get = MagicMock(return_value=None)
        vh.log_version = AsyncMock()
        al.connect = AsyncMock()
        al.record = AsyncMock()

        result = await agent._commit_push("test message", "v1")

    assert "failed" in result.lower()
    al.record.assert_called_once()
    kwargs = al.record.call_args.kwargs
    assert kwargs["success"] is False
    assert kwargs["action"] == "commit_and_push"
