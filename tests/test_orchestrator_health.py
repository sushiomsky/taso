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
