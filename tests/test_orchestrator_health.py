"""
Tests for orchestrator startup health-check behavior.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestOrchestratorStartupHealth:
    @pytest.mark.asyncio
    async def test_startup_health_check_passes(self):
        from orchestrator import Orchestrator

        orch = Orchestrator()
        report = MagicMock()
        report.passed = True
        report.failed_checks = []
        report.duration.return_value = 0.12

        with patch("self_healing.health_checker.health_checker") as hc_mod:
            hc_mod.check_all = AsyncMock(return_value=report)
            ok = await orch._run_startup_health_check()

        assert ok is True
        hc_mod.check_all.assert_called_once_with(quick=True)

    @pytest.mark.asyncio
    async def test_startup_health_check_failure_is_nonfatal(self):
        from orchestrator import Orchestrator

        orch = Orchestrator()
        report = MagicMock()
        report.passed = False
        report.failed_checks = ["sandbox", "tools"]

        with patch("self_healing.health_checker.health_checker") as hc_mod:
            hc_mod.check_all = AsyncMock(return_value=report)
            ok = await orch._run_startup_health_check()

        assert ok is False
        hc_mod.check_all.assert_called_once_with(quick=True)

    @pytest.mark.asyncio
    async def test_startup_health_check_handles_exception(self):
        from orchestrator import Orchestrator

        orch = Orchestrator()

        with patch("self_healing.health_checker.health_checker") as hc_mod:
            hc_mod.check_all = AsyncMock(side_effect=RuntimeError("boom"))
            ok = await orch._run_startup_health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_run_cleans_up_on_keyboard_interrupt(self):
        from orchestrator import Orchestrator

        orch = Orchestrator()
        bus = MagicMock()
        bus.stop = AsyncMock()
        db = MagicMock()
        db.close = AsyncMock()
        conv_store = MagicMock()
        conv_store.close = AsyncMock()
        bot = MagicMock()
        bot.stop = AsyncMock()
        agent = MagicMock()
        agent.name = "agent-x"
        agent.stop = AsyncMock()

        with patch("orchestrator.init_logging"), \
             patch.object(orch, "_log_startup_info"), \
             patch.object(orch, "_start_message_bus", new_callable=AsyncMock, return_value=bus), \
             patch.object(orch, "_initialize_memory_subsystem", new_callable=AsyncMock, return_value=(db, MagicMock(), conv_store)), \
             patch.object(orch, "_initialize_tool_registry", return_value=MagicMock()), \
             patch.object(orch, "_start_agents", new_callable=AsyncMock, return_value=[agent]), \
             patch.object(orch, "_initialize_optional_features", new_callable=AsyncMock), \
             patch.object(orch, "_run_startup_health_check", new_callable=AsyncMock, return_value=True), \
             patch.object(orch, "_start_telegram_bot", new_callable=AsyncMock, return_value=bot), \
             patch.object(orch, "_start_log_monitor", new_callable=AsyncMock), \
             patch.object(orch, "_wait_for_shutdown", new_callable=AsyncMock, side_effect=KeyboardInterrupt()):
            with pytest.raises(KeyboardInterrupt):
                await orch.run()

        bot.stop.assert_called_once()
        agent.stop.assert_called_once()
        bus.stop.assert_called_once()
        db.close.assert_called_once()
        conv_store.close.assert_called_once()
