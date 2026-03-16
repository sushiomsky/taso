"""
TASO – Telegram Command Tests

Tests every registered bot command handler. Validates that:
  - Handler replies to the user (calls reply_text)
  - Does not crash or raise unhandled exceptions
  - Enforces admin auth where required
  - Handles missing args gracefully

External dependencies mocked: bus, LLM, git, Docker, DB.
_dispatch_task is patched at method-level to return safe payloads.

Run before every commit (integrated into DevLifecycle test stage).
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Constants & shared helpers
# ──────────────────────────────────────────────────────────────────────────────

ADMIN_ID    = 7083606259
STRANGER_ID = 9999999

_SETTINGS_PATCH = {
    "TELEGRAM_ADMIN_IDS":       [ADMIN_ID],
    "TELEGRAM_ADMIN_USERNAMES": ["yzymowep"],
    "TELEGRAM_BOT_TOKEN":       "FAKE:TOKEN",
    "OLLAMA_BASE_URL":          "http://localhost:11434",
    "GIT_REPO_PATH":            "/root/taso",
}


def _make_update(
    text: str = "/status",
    user_id: int = ADMIN_ID,
    username: str = "yzymowep",
) -> MagicMock:
    update                         = MagicMock()
    update.effective_user          = MagicMock()
    update.effective_user.id       = user_id
    update.effective_user.username = username
    update.effective_chat          = MagicMock()
    update.effective_chat.id       = user_id
    update.message                 = MagicMock()
    update.message.text            = text
    update.message.from_user       = update.effective_user
    update.message.reply_text      = AsyncMock(return_value=MagicMock())
    update.message.reply_document  = AsyncMock(return_value=MagicMock())
    return update


def _ctx(args: List[str] = None) -> MagicMock:
    ctx             = MagicMock()
    ctx.args        = args or []
    ctx.user_data   = {}       # real dict so .get() / .pop() work correctly
    ctx.bot         = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _stranger() -> MagicMock:
    return _make_update(user_id=STRANGER_ID, username="stranger")


def _patch_dispatch(bot, payload: dict = None):
    """Patch bot._dispatch_task to return payload without touching the bus."""
    return patch.object(
        bot, "_dispatch_task",
        new_callable=AsyncMock,
        return_value=payload or {"result": {}, "status": "completed"},
    )


def _patch_dispatch_and_settings(bot, payload: dict = None):
    """Combined patch: settings + _dispatch_task."""
    class _CM:
        def __init__(self):
            self._s = patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH)
            self._d = _patch_dispatch(bot, payload)
        def __enter__(self):
            self._s.__enter__()
            return self._d.__enter__()
        def __exit__(self, *a):
            self._d.__exit__(*a)
            self._s.__exit__(*a)
    return _CM()


# ──────────────────────────────────────────────────────────────────────────────
# Bot fixture
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def bot_instance():
    """TelegramBot with all external dependencies mocked."""
    from agents.message_bus import BusMessage
    from bot.telegram_bot import TelegramBot

    reply_msg = BusMessage(topic="r", sender="a", payload={"result": {}, "status": "ok"})
    bus = MagicMock()
    bus.publish_and_wait = AsyncMock(return_value=reply_msg)
    bus.subscribe        = MagicMock()
    bus.unsubscribe      = MagicMock()

    coord = MagicMock()
    coord.handle_message = AsyncMock(return_value="Response text")
    coord.list_tasks     = MagicMock(return_value=[])

    conv = MagicMock()
    conv.save        = AsyncMock()
    conv.add_message = AsyncMock()
    conv.get_context = AsyncMock(return_value=[])
    conv.history     = AsyncMock(return_value=[])

    tools = MagicMock()
    tools.list_tools = MagicMock(return_value=[
        {"name": f"tool_{i}", "description": f"Desc {i}", "dynamic": False}
        for i in range(3)
    ])
    tools.describe_all_tools = MagicMock(return_value=[
        {"name": f"tool_{i}", "description": f"Desc {i}", "dynamic": False}
        for i in range(3)
    ] + [{"name": "dyn_tool", "description": "AI-generated", "dynamic": True}])

    with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
        b = TelegramBot(bus, coord, conv, tools)

    return b, bus, coord, conv, tools


def _replied(update: MagicMock) -> str:
    """Return the first reply text sent to the update."""
    return update.message.reply_text.call_args[0][0]


def _assert_replied(update: MagicMock) -> None:
    update.message.reply_text.assert_called()


def _assert_auth_error(update: MagicMock) -> None:
    _assert_replied(update)
    text = _replied(update).lower()
    assert any(w in text for w in ("unauthori", "not allowed", "access denied", "admin", "403"))


# ──────────────────────────────────────────────────────────────────────────────
# Auth / _guard
# ──────────────────────────────────────────────────────────────────────────────

class TestBotGuard:
    @pytest.mark.asyncio
    async def test_admin_allowed(self, bot_instance):
        bot, *_ = bot_instance
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            ok = await bot._guard(_make_update(), _ctx(), admin_required=False)
        assert ok is True

    @pytest.mark.asyncio
    async def test_stranger_blocked_on_admin_cmd(self, bot_instance):
        bot, *_ = bot_instance
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            ok = await bot._guard(_stranger(), _ctx(), admin_required=True)
        assert ok is False

    @pytest.mark.asyncio
    async def test_stranger_allowed_on_public_cmd(self, bot_instance):
        bot, *_ = bot_instance
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            ok = await bot._guard(_stranger(), _ctx(), admin_required=False)
        assert ok is True


# ──────────────────────────────────────────────────────────────────────────────
# /status
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdStatus:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": {"metrics": {}}}):
            await bot._cmd_status(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_stranger_blocked(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_status(update, _ctx())
        _assert_auth_error(update)


# ──────────────────────────────────────────────────────────────────────────────
# /tools  — bug fixed: now uses describe_all_tools() instead of list_tools()
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdTools:
    @pytest.mark.asyncio
    async def test_lists_static_and_dynamic(self, bot_instance):
        bot, _, _, _, tools = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_tools(update, _ctx())
        _assert_replied(update)
        text = _replied(update)
        assert "tool_0" in text
        assert "dyn_tool" in text or "Dynamic" in text

    @pytest.mark.asyncio
    async def test_empty_registry_helpful_message(self, bot_instance):
        bot, _, _, _, tools = bot_instance
        tools.describe_all_tools.return_value = []
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_tools(update, _ctx())
        text = _replied(update)
        assert "No tools" in text or "create_tool" in text

    @pytest.mark.asyncio
    async def test_shows_builtin_and_dynamic_sections(self, bot_instance):
        bot, _, _, _, tools = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_tools(update, _ctx())
        text = _replied(update)
        assert "Built-in" in text or "Dynamic" in text


# ──────────────────────────────────────────────────────────────────────────────
# /agents
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdAgents:
    @pytest.mark.asyncio
    async def test_no_tasks_replies(self, bot_instance):
        bot, _, coord, *_ = bot_instance
        coord.list_tasks.return_value = []
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_agents(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_shows_task_list(self, bot_instance):
        bot, _, coord, *_ = bot_instance
        coord.list_tasks.return_value = [{
            "id": "abc123", "command": "security_scan",
            "status": "done", "created_at": "2026-01-01T00:00:00",
        }]
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_agents(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /memory
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdMemory:
    @pytest.mark.asyncio
    async def test_no_args_requires_query(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_memory(update, _ctx(args=[]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_with_query_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        payload = {"results": [{"content": "CVE data"}], "count": 1}
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, payload):
            await bot._cmd_memory(update, _ctx(args=["CVE-2024"]))
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /logs
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdLogs:
    @pytest.mark.asyncio
    async def test_stranger_blocked(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_logs(update, _ctx())
        _assert_auth_error(update)

    @pytest.mark.asyncio
    async def test_admin_gets_content(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": "line1\nline2"}), \
             patch("agents.system_agent.SystemAgent._read_log", return_value=["l1", "l2"]):
            await bot._cmd_logs(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /system
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdSystem:
    @pytest.mark.asyncio
    async def test_delegates_to_status(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_cmd_status", new_callable=AsyncMock) as mock_s:
            await bot._cmd_system(update, _ctx())
        mock_s.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# /swarm_status
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdSwarmStatus:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        mock_swarm = MagicMock()
        mock_swarm.status.return_value = {
            "active_swarms": 0, "completed_swarms": 2,
            "max_parallel": 5, "task_timeout": 300, "recent": [],
        }
        # Handler does: from swarm.swarm_orchestrator import swarm_orchestrator
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("swarm.swarm_orchestrator.swarm_orchestrator", mock_swarm):
            await bot._cmd_swarm_status(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_stranger_blocked(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_swarm_status(update, _ctx())
        _assert_auth_error(update)


# ──────────────────────────────────────────────────────────────────────────────
# /swarm_agents
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdSwarmAgents:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("swarm.agent_registry.agent_registry") as ar:
            ar.status_dict.return_value = {}
            await bot._cmd_swarm_agents(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /swarm_models
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdSwarmModels:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("models.model_registry.registry") as reg:
            reg.all_models.return_value = []
            await bot._cmd_swarm_models(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /run_swarm_task
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdRunSwarmTask:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_run_swarm_task(update, _ctx(args=[]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_dispatches_with_task(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": "Queued", "task_id": "t1"}):
            await bot._cmd_run_swarm_task(update, _ctx(args=["scan", "CVEs"]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_dispatch_error_does_not_fallback_to_direct_swarm(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_dispatch_task", new_callable=AsyncMock, side_effect=RuntimeError("bus timeout")), \
             patch("swarm.swarm_orchestrator.swarm_orchestrator") as mock_swarm:
            mock_swarm.run = AsyncMock(return_value="should-not-run")
            await bot._cmd_run_swarm_task(update, _ctx(args=["scan", "CVEs"]))

        text = _replied(update)
        assert "dispatch error" in text.lower()
        mock_swarm.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_routed_error_payload_returns_error_not_success(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": {"error": "agent refused"}, "task_id": "t-err-1"}):
            await bot._cmd_run_swarm_task(update, _ctx(args=["scan", "CVEs"]))

        text = _replied(update).lower()
        assert "swarm error" in text
        assert "agent refused" in text
        assert "task id: t-err-1" in text


# ──────────────────────────────────────────────────────────────────────────────
# /model_router
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdModelRouter:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_model_router(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /system_status
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdSystemStatus:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        metrics = {"cpu": {"percent": 5}, "memory": {}, "disk": {}, "docker": {},
                   "hostname": "test", "python": "3.11", "boot_time": "2026-01-01T00:00:00"}
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": {"metrics": metrics}}):
            await bot._cmd_system_status(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /scan_repo
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdScanRepo:
    @pytest.mark.asyncio
    async def test_scans_default_path_when_no_args(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": {"summary": "Clean", "findings": {}}}):
            await bot._cmd_scan_repo(update, _ctx(args=[]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_scans_given_url(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": {"summary": "0 issues", "findings": {}}}):
            await bot._cmd_scan_repo(update, _ctx(args=["https://github.com/x/y"]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_stranger_blocked(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_scan_repo(update, _ctx())
        _assert_auth_error(update)


# ──────────────────────────────────────────────────────────────────────────────
# /security_scan
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdSecurityScan:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": {"summary": "No critical vulns"}}):
            await bot._cmd_security_scan(update, _ctx(args=["."]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_stranger_blocked(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_security_scan(update, _ctx())
        _assert_auth_error(update)


# ──────────────────────────────────────────────────────────────────────────────
# /code_audit
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdCodeAudit:
    @pytest.mark.asyncio
    async def test_prompts_for_code(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        ctx = _ctx()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_code_audit(update, ctx)
        _assert_replied(update)
        # Handler sets user_data flag
        assert ctx.user_data.get("awaiting_code_audit") is True

    @pytest.mark.asyncio
    async def test_stranger_blocked(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_code_audit(update, _ctx())
        _assert_auth_error(update)


# ──────────────────────────────────────────────────────────────────────────────
# /threat_intel
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdThreatIntel:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        intel = {"summary": "Done", "gathered": {"nvd": {"items": []}, "cisa": {"items": []}}}
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": intel}):
            await bot._cmd_threat_intel(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /update_self
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdUpdateSelf:
    @pytest.mark.asyncio
    async def test_stranger_blocked(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_update_self(update, _ctx())
        _assert_auth_error(update)

    @pytest.mark.asyncio
    async def test_admin_triggers(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": "Initiated"}):
            await bot._cmd_update_self(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /dev_status
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDevStatus:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": "Dev OK"}):
            await bot._cmd_dev_status(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /dev_rollback  — NOTE: admin_required=False (any authenticated user)
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDevRollback:
    @pytest.mark.asyncio
    async def test_replies_on_success(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.rollback_manager.rollback_manager") as rm:
            rm.rollback = AsyncMock(return_value="abc12345")
            await bot._cmd_dev_rollback(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_replies_on_failure(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.rollback_manager.rollback_manager") as rm:
            rm.rollback = AsyncMock(return_value=None)
            await bot._cmd_dev_rollback(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /dev_deploy  — NOTE: admin_required=False
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDevDeploy:
    @pytest.mark.asyncio
    async def test_replies_on_success(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.deploy_manager.deploy_manager") as dm:
            dm.bootstrap    = AsyncMock(return_value=True)
            dm.current_sha  = "abc12345"
            await bot._cmd_dev_deploy(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /models
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdModels:
    @pytest.mark.asyncio
    async def test_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("models.model_registry.registry") as reg:
            reg.all_models.return_value = []
            await bot._cmd_models(update, _ctx())
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /create_tool
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdCreateTool:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_create_tool(update, _ctx(args=[]))
        text = _replied(update)
        assert "Usage" in text or "usage" in text or "create_tool" in text

    @pytest.mark.asyncio
    async def test_with_description_dispatches(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_dispatch", new_callable=AsyncMock,
                          return_value={"result": "port_scanner generated"}):
            await bot._cmd_create_tool(update, _ctx(args=["port", "scanner"]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_with_description_uses_tokenized_callback_payload(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        ctx = _ctx(args=["port", "scanner"])
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_create_tool(update, ctx)

        kwargs = update.message.reply_text.call_args.kwargs
        kb = kwargs["reply_markup"]
        yes_cb = kb.inline_keyboard[0][0].callback_data
        no_cb = kb.inline_keyboard[0][1].callback_data
        assert yes_cb.startswith("gentool_ref:")
        assert no_cb.startswith("gentool_ref:")
        assert "port scanner" not in yes_cb


# ──────────────────────────────────────────────────────────────────────────────
# /create_agent
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdCreateAgent:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_create_agent(update, _ctx(args=[]))
        text = _replied(update)
        assert "Usage" in text or "usage" in text or "create_agent" in text

    @pytest.mark.asyncio
    async def test_with_description_dispatches(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_dispatch", new_callable=AsyncMock,
                          return_value={"result": "Agent created"}):
            await bot._cmd_create_agent(update, _ctx(args=["threat", "monitor"]))
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /dev_sync  (new)
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDevSync:
    @pytest.mark.asyncio
    async def test_replies_up_to_date(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        mock_lc = MagicMock()
        mock_lc.sync_repo = AsyncMock(return_value={
            "success": True, "new_commits": [],
            "previous_sha": "abc", "current_sha": "abc",
        })
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.dev_lifecycle.dev_lifecycle", mock_lc):
            await bot._cmd_dev_sync(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_shows_new_commit_count(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        mock_lc = MagicMock()
        mock_lc.sync_repo = AsyncMock(return_value={
            "success": True,
            "new_commits": [{"sha": "abc12345", "message": "feat: new thing"}],
            "previous_sha": "old", "current_sha": "abc12345",
        })
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.dev_lifecycle.dev_lifecycle", mock_lc):
            await bot._cmd_dev_sync(update, _ctx())
        text = _replied(update)
        assert "abc12345" in text or "1" in text


# ──────────────────────────────────────────────────────────────────────────────
# /dev_health  (new)
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDevHealth:
    @pytest.mark.asyncio
    async def test_healthy_report(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        from self_healing.health_checker import HealthReport, CheckResult
        mock_report = HealthReport(checks=[CheckResult("imports", True), CheckResult("tools", True)])
        mock_hc = MagicMock()
        mock_hc.check_all = AsyncMock(return_value=mock_report)
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.health_checker.health_checker", mock_hc):
            await bot._cmd_dev_health(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_unhealthy_shows_failures(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        from self_healing.health_checker import HealthReport, CheckResult
        mock_report = HealthReport(checks=[
            CheckResult("imports", True),
            CheckResult("sandbox", False, "container error"),
        ])
        mock_hc = MagicMock()
        mock_hc.check_all = AsyncMock(return_value=mock_report)
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.health_checker.health_checker", mock_hc):
            await bot._cmd_dev_health(update, _ctx())
        text = _replied(update)
        assert "❌" in text or "UNHEALTHY" in text or "sandbox" in text


# ──────────────────────────────────────────────────────────────────────────────
# /dev_lifecycle  (new)
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDevLifecycle:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_dev_lifecycle(update, _ctx(args=[]))
        text = _replied(update)
        assert "Usage" in text or "usage" in text or "dev_lifecycle" in text

    @pytest.mark.asyncio
    async def test_with_description_dispatches(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_dispatch", new_callable=AsyncMock,
                          return_value={"summary": "Pipeline complete"}):
            await bot._cmd_dev_lifecycle(update, _ctx(args=["refactor", "sandbox"]))
        _assert_replied(update)


# ──────────────────────────────────────────────────────────────────────────────
# /dev_branches  (new)
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDevBranches:
    @pytest.mark.asyncio
    async def test_no_feature_branches_message(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.git_manager.git_list_branches",
                   new_callable=AsyncMock, return_value=["main"]):
            await bot._cmd_dev_branches(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_shows_feature_branches(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("self_healing.git_manager.git_list_branches",
                   new_callable=AsyncMock,
                   return_value=["main", "bot/dev/feat-x", "bot/dev/fix-y"]):
            await bot._cmd_dev_branches(update, _ctx())
        text = _replied(update)
        assert "feat-x" in text or "bot/dev" in text


# ──────────────────────────────────────────────────────────────────────────────
# Natural-language routing via _handle_message
# ──────────────────────────────────────────────────────────────────────────────

class TestNaturalLanguageRouting:
    @pytest.mark.asyncio
    async def test_plain_text_gets_nlp_response(self, bot_instance):
        bot, _, coord, conv, _ = bot_instance
        update = _make_update("help me scan for vulnerabilities")
        ctx = _ctx()
        # Patch classify_intent to return plain chat so we don't invoke sub-handlers
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_classify_intent", new_callable=AsyncMock,
                          return_value={"intent": "chat", "arg": "help me", "confidence": 1.0}), \
             patch.object(bot, "_nlp_chat", new_callable=AsyncMock,
                          return_value="I can help with that."):
            await bot._handle_message(update, ctx)
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_awaiting_code_audit_state(self, bot_instance):
        """When user_data has awaiting_code_audit, next message is audited."""
        bot, *_ = bot_instance
        update = _make_update("print('hello')")
        ctx = _ctx()
        ctx.user_data["awaiting_code_audit"] = True
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             _patch_dispatch(bot, {"result": {"analysis": "Looks clean."}}):
            await bot._handle_message(update, ctx)
        _assert_replied(update)
        assert "awaiting_code_audit" not in ctx.user_data   # flag was consumed

    @pytest.mark.asyncio
    async def test_stranger_blocked_in_handle_message(self, bot_instance):
        bot, *_ = bot_instance
        update = _stranger()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._handle_message(update, _ctx())
        _assert_auth_error(update)

    @pytest.mark.asyncio
    async def test_question_like_action_prefers_chat(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update("can you create a tool to scan ports?")
        ctx = _ctx()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_classify_intent", new_callable=AsyncMock, return_value={
                 "intent": "create_tool", "arg": "scan ports", "confidence": 0.72
             }), \
             patch.object(bot, "_nlp_chat", new_callable=AsyncMock, return_value="Sure, here is how."), \
             patch.object(bot, "_nlp_create_tool", new_callable=AsyncMock) as mock_create_tool:
            await bot._handle_message(update, ctx)

        mock_create_tool.assert_not_called()
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_low_confidence_clarification_uses_tokenized_callbacks(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update("maybe run something")
        ctx = _ctx()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch.object(bot, "_classify_intent", new_callable=AsyncMock, return_value={
                 "intent": "swarm_task", "arg": "maybe run something", "confidence": 0.35
             }):
            await bot._handle_message(update, ctx)

        kwargs = update.message.reply_text.call_args.kwargs
        kb = kwargs["reply_markup"]
        yes_cb = kb.inline_keyboard[0][0].callback_data
        chat_cb = kb.inline_keyboard[0][1].callback_data
        assert yes_cb.startswith("confirm_ref:")
        assert chat_cb.startswith("confirm_ref:")


# ──────────────────────────────────────────────────────────────────────────────
# _reply_long
# ──────────────────────────────────────────────────────────────────────────────

class TestReplyLong:
    @pytest.mark.asyncio
    async def test_short_message_single_call(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        await bot._reply_long(update, "short message")
        assert update.message.reply_text.call_count == 1

    @pytest.mark.asyncio
    async def test_long_message_split(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        await bot._reply_long(update, "x" * 10000)
        assert update.message.reply_text.call_count >= 3

    @pytest.mark.asyncio
    async def test_exactly_chunk_boundary(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        await bot._reply_long(update, "y" * 4000)
        assert update.message.reply_text.call_count == 1


# ──────────────────────────────────────────────────────────────────────────────
# Crawler commands
# ──────────────────────────────────────────────────────────────────────────────

class TestCrawlerCommands:
    @pytest.mark.asyncio
    async def test_crawl_start_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        mock_cm = MagicMock()
        mock_cm.start_onion = AsyncMock(return_value="onion started")
        mock_cm.start_clearnet = AsyncMock(return_value="clearnet started")
        mock_cm.start_irc = AsyncMock(return_value="irc started")
        mock_cm.start_newsgroup = AsyncMock(return_value="news started")

        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("crawler.crawler_manager.crawler_manager", mock_cm):
            await bot._cmd_crawl_start(update, _ctx(args=[]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_crawl_status_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        mock_cm = MagicMock()
        mock_cm.status = AsyncMock(return_value={"crawlers": {}, "db": {"queue": {}}})
        mock_cm.format_status = MagicMock(return_value="crawler status")

        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("crawler.crawler_manager.crawler_manager", mock_cm):
            await bot._cmd_crawl_status(update, _ctx())
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_crawl_add_requires_url(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH):
            await bot._cmd_crawl_add(update, _ctx(args=[]))
        text = _replied(update)
        assert "Usage" in text or "crawl_add" in text

    @pytest.mark.asyncio
    async def test_crawl_search_with_results(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        mock_cm = MagicMock()
        mock_cm.search = AsyncMock(return_value=[
            {
                "type": "page",
                "title": "Security Advisory",
                "url": "https://example.com/advisory",
                "source_type": "clearnet",
                "snippet": "important security update",
            }
        ])

        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("crawler.crawler_manager.crawler_manager", mock_cm):
            await bot._cmd_crawl_search(update, _ctx(args=["security"]))
        _assert_replied(update)

    @pytest.mark.asyncio
    async def test_crawl_onions_replies(self, bot_instance):
        bot, *_ = bot_instance
        update = _make_update()
        mock_cm = MagicMock()
        mock_cm.get_onions = AsyncMock(return_value=[
            {"address": "exampleexample.onion", "title": "Forum", "times_seen": 2, "status": "alive"}
        ])
        mock_cm._db = MagicMock()
        mock_cm._db.count_onions = AsyncMock(return_value=1)

        with patch.multiple("bot.telegram_bot.settings", **_SETTINGS_PATCH), \
             patch("crawler.crawler_manager.crawler_manager", mock_cm):
            await bot._cmd_crawl_onions(update, _ctx(args=[]))
        _assert_replied(update)
