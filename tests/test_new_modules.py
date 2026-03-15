"""
Tests for new modules: MonitoringAgent, system_tools, AuditLog,
DockerRunner class, OllamaClient class, SecurityAgent alias,
ToolRegistry re-export, tool_registry module.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# SecurityAgent alias
# ---------------------------------------------------------------------------

class TestSecurityAgentAlias:
    def test_alias_importable(self):
        from agents.security_agent import SecurityAgent, SecurityAnalysisAgent
        assert SecurityAgent is SecurityAnalysisAgent

    def test_class_has_name(self):
        from agents.security_agent import SecurityAgent
        assert SecurityAgent.name == "security"


# ---------------------------------------------------------------------------
# OllamaClient class
# ---------------------------------------------------------------------------

class TestOllamaClient:
    def test_importable(self):
        from models.ollama_client import OllamaClient
        client = OllamaClient(base_url="http://localhost:11434")
        assert client.base_url == "http://localhost:11434"

    def test_default_base_url(self):
        from models.ollama_client import OllamaClient
        client = OllamaClient()
        assert "11434" in client.base_url or "localhost" in client.base_url

    @pytest.mark.asyncio
    async def test_health_method_exists(self):
        from models.ollama_client import OllamaClient
        client = OllamaClient()
        # patch the underlying function so no real HTTP call is made
        with patch("models.ollama_client.ollama_health", new_callable=AsyncMock, return_value=True):
            result = await client.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_list_models_method(self):
        from models.ollama_client import OllamaClient
        client = OllamaClient()
        with patch(
            "models.ollama_client.ollama_list_models",
            new_callable=AsyncMock,
            return_value=["llama3", "dolphin-mistral"],
        ):
            models = await client.list_models()
        assert "llama3" in models


# ---------------------------------------------------------------------------
# DockerRunner class
# ---------------------------------------------------------------------------

class TestDockerRunner:
    def test_importable(self):
        from sandbox.docker_runner import DockerRunner
        runner = DockerRunner(image="python:3.11-slim", timeout=10)
        assert runner.image == "python:3.11-slim"
        assert runner.timeout == 10

    def test_defaults(self):
        from sandbox.docker_runner import DockerRunner, ContainerResult
        runner = DockerRunner()
        assert runner.image  # non-empty
        assert runner.timeout > 0

    @pytest.mark.asyncio
    async def test_run_code_delegates(self):
        from sandbox.docker_runner import DockerRunner, ContainerResult
        fake = ContainerResult(exit_code=0, stdout="hi", stderr="")
        with patch("sandbox.docker_runner.run_code", new_callable=AsyncMock, return_value=fake):
            runner = DockerRunner()
            result = await runner.run_code("print('hi')")
        assert result.success


# ---------------------------------------------------------------------------
# tool_registry re-export
# ---------------------------------------------------------------------------

class TestToolRegistryReExport:
    def test_importable(self):
        from tools.tool_registry import ToolRegistry, registry, BaseTool, ToolSchema
        assert ToolRegistry is not None
        assert registry is not None

    def test_registry_is_same_singleton(self):
        from tools.tool_registry import registry as r1
        from tools.base_tool import registry as r2
        assert r1 is r2


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class TestAuditLog:
    @pytest.fixture
    def audit(self, tmp_path):
        from memory.audit_log import AuditLog
        return AuditLog(path=tmp_path / "audit.db")

    @pytest.mark.asyncio
    async def test_connect(self, audit):
        await audit.connect()
        assert audit._ready

    @pytest.mark.asyncio
    async def test_record_and_query(self, audit):
        await audit.connect()
        row_id = await audit.record(
            agent="test_agent",
            action="test_action",
            input_summary="some input",
            output_summary="some output",
            success=True,
        )
        assert row_id > 0
        entries = await audit.query(agent="test_agent")
        assert len(entries) == 1
        assert entries[0].agent == "test_agent"
        assert entries[0].action == "test_action"
        assert entries[0].success is True

    @pytest.mark.asyncio
    async def test_record_failure(self, audit):
        await audit.connect()
        await audit.record(
            agent="security_agent",
            action="code_audit",
            success=False,
            error="timeout",
        )
        entries = await audit.query(success=False)
        assert len(entries) == 1
        assert entries[0].error == "timeout"

    @pytest.mark.asyncio
    async def test_stats(self, audit):
        await audit.connect()
        await audit.record("a1", "x", success=True)
        await audit.record("a2", "y", success=False)
        stats = await audit.stats()
        assert stats["total_entries"] == 2
        assert "a2" in stats["failures_by_agent"]

    @pytest.mark.asyncio
    async def test_format_recent(self, audit):
        await audit.connect()
        await audit.record("bot", "start", output_summary="started ok")
        text = await audit.format_recent(5)
        assert "bot" in text or "started ok" in text

    @pytest.mark.asyncio
    async def test_input_hash_computed(self, audit):
        await audit.connect()
        await audit.record("agent", "act", input_summary="hello world")
        entries = await audit.query()
        # hash is non-empty when input_summary is non-empty
        assert entries[0].input_hash != ""


# ---------------------------------------------------------------------------
# MonitoringAgent
# ---------------------------------------------------------------------------

class TestMonitoringAgent:
    def test_importable(self):
        from agents.monitoring_agent import MonitoringAgent
        assert MonitoringAgent.name == "monitoring"

    def test_collect_sync(self):
        from agents.monitoring_agent import MonitoringAgent
        snap = MonitoringAgent._collect_sync()
        assert "cpu_pct" in snap
        assert "mem_pct" in snap
        assert "disk_pct" in snap

    def test_snapshot_summary(self):
        from agents.monitoring_agent import MonitoringAgent
        snap = {"cpu_pct": 10, "mem_pct": 50, "disk_pct": 30}
        summary = MonitoringAgent._snapshot_summary(None, snap)
        assert "CPU" in summary and "10" in summary

    def test_check_thresholds_normal(self):
        from agents.monitoring_agent import MonitoringAgent
        bus  = MagicMock()
        agent = MonitoringAgent.__new__(MonitoringAgent)
        agent._alerts = []
        snap = {"ts": time.time(), "cpu_pct": 20, "mem_pct": 40, "disk_pct": 50}
        agent._check_thresholds(snap)
        assert len(agent._alerts) == 0  # no thresholds exceeded

    def test_check_thresholds_critical(self):
        from agents.monitoring_agent import MonitoringAgent
        agent = MonitoringAgent.__new__(MonitoringAgent)
        agent._alerts = []
        snap = {"ts": time.time(), "cpu_pct": 95, "mem_pct": 30, "disk_pct": 30}
        agent._check_thresholds(snap)
        assert len(agent._alerts) == 1
        assert "CPU" in agent._alerts[0]["message"]


# ---------------------------------------------------------------------------
# system_tools
# ---------------------------------------------------------------------------

class TestSystemTools:
    def test_importable(self):
        from tools.system_tools import (
            PortScannerTool, ProcessListerTool, NetworkCheckTool,
            DiskUsageTool, EnvInspectorTool,
        )

    @pytest.mark.asyncio
    async def test_disk_usage(self):
        from tools.system_tools import DiskUsageTool
        tool   = DiskUsageTool()
        result = await tool.execute(paths=["/"])
        assert "/" in result["paths"]
        assert result["paths"]["/"].get("total_gb", 0) > 0

    @pytest.mark.asyncio
    async def test_env_inspector_redacts(self):
        import os
        from tools.system_tools import EnvInspectorTool
        os.environ["_TASO_TEST_SECRET_KEY"] = "super_secret_value"
        tool   = EnvInspectorTool()
        result = await tool.execute(prefix="_TASO_TEST", redact=True)
        assert result["variables"].get("_TASO_TEST_SECRET_KEY") == "***REDACTED***"
        del os.environ["_TASO_TEST_SECRET_KEY"]

    @pytest.mark.asyncio
    async def test_process_lister(self):
        from tools.system_tools import ProcessListerTool
        tool   = ProcessListerTool()
        result = await tool.execute(limit=5)
        assert result["count"] <= 5
        assert isinstance(result["processes"], list)

    @pytest.mark.asyncio
    async def test_port_scanner_localhost(self):
        """Localhost port 22 may or may not be open – just check it runs."""
        from tools.system_tools import PortScannerTool
        tool   = PortScannerTool()
        result = await tool.execute(host="127.0.0.1", ports=[22, 80], timeout=0.5)
        assert "open_ports" in result
        assert "closed_ports" in result
        assert result["scanned"] == 2

    @pytest.mark.asyncio
    async def test_network_check_invalid(self):
        """Fake host should return reachable=False."""
        from tools.system_tools import NetworkCheckTool
        tool   = NetworkCheckTool()
        result = await tool.execute(hosts=["this.host.does.not.exist"], port=80, timeout=1.0)
        assert not result["results"]["this.host.does.not.exist"]["reachable"]
