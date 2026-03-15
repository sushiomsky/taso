"""
tests/test_dynamic_tool_pipeline.py

Tests for the dynamic tool creation pipeline:
  - ToolRegistry: register_dynamic, call_tool, tool_exists,
                  describe_all_tools, save/load persistence
  - sandbox_tester: safe code, bad code, timeout
  - PlannerAgent: missing tool detection via _CAPABILITY_TOOL_MAP
  - SecurityAgent: security.test_tool bus handler
  - MemoryAgent: memory.store_tool bus handler
  - DeveloperAgent: bus topic subscriptions, _handle_dev_request routing
  - tool_persistence: persist_tool, load_all, list_persisted, delete_persisted
  - BaseAgent: call_tool, tool_exists, list_available_tools helpers
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ─────────────────────────── project root on path ───────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def fresh_registry():
    """A ToolRegistry instance with no tools (bypasses discover())."""
    from tools.base_tool import ToolRegistry
    return ToolRegistry()


@pytest.fixture
def simple_tool_code():
    return """
def run_tool(input_data: dict) -> dict:
    return {"success": True, "result": input_data.get("value", 42), "error": None}
"""


@pytest.fixture
def broken_tool_code():
    return """
def run_tool(input_data: dict) -> dict:
    raise RuntimeError("intentional failure")
"""


@pytest.fixture
def no_runtool_code():
    return """
def wrong_name(input_data: dict) -> dict:
    return {}
"""


# ===========================================================================
# 1. ToolRegistry: core dynamic API
# ===========================================================================

class TestToolRegistryDynamic:

    def test_register_dynamic_success(self, fresh_registry, simple_tool_code, tmp_path):
        """register_dynamic stores the async wrapper and returns True."""
        with patch("tools.base_tool.Path", lambda *a, **kw: tmp_path / "dynamic_tools"):
            ok = fresh_registry.register_dynamic(
                name="test_tool",
                code=simple_tool_code,
                description="A test tool",
            )
        assert ok is True
        assert "test_tool" in fresh_registry.list_dynamic()

    def test_register_dynamic_duplicate(self, fresh_registry, simple_tool_code, tmp_path):
        """Registering the same name twice returns False (duplicate guard)."""
        with patch("tools.base_tool.Path", lambda *a, **kw: tmp_path / "dynamic_tools"):
            fresh_registry.register_dynamic("dup_tool", simple_tool_code, "desc")
            ok = fresh_registry.register_dynamic("dup_tool", simple_tool_code, "desc")
        assert ok is False

    def test_register_dynamic_no_runtool(self, fresh_registry, no_runtool_code, tmp_path):
        """Code without run_tool() must return False."""
        with patch("tools.base_tool.Path", lambda *a, **kw: tmp_path / "dynamic_tools"):
            ok = fresh_registry.register_dynamic("bad", no_runtool_code, "desc")
        assert ok is False

    def test_tool_exists(self, fresh_registry, simple_tool_code, tmp_path):
        with patch("tools.base_tool.Path", lambda *a, **kw: tmp_path / "dynamic_tools"):
            fresh_registry.register_dynamic("exists_tool", simple_tool_code, "desc")
        assert fresh_registry.tool_exists("exists_tool") is True
        assert fresh_registry.tool_exists("missing_tool") is False

    @pytest.mark.asyncio
    async def test_call_tool_dynamic(self, fresh_registry, simple_tool_code, tmp_path):
        """call_tool dispatches to the dynamic run_tool function."""
        with patch("tools.base_tool.Path", lambda *a, **kw: tmp_path / "dynamic_tools"):
            fresh_registry.register_dynamic("callable_tool", simple_tool_code, "desc")
        result = await fresh_registry.call_tool("callable_tool", value=99)
        assert result["success"] is True
        assert result["result"] == 99

    @pytest.mark.asyncio
    async def test_call_tool_missing(self, fresh_registry):
        """call_tool for a non-existent tool returns success=False."""
        result = await fresh_registry.call_tool("ghost_tool")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_describe_all_tools_includes_dynamic(self, fresh_registry, simple_tool_code, tmp_path):
        with patch("tools.base_tool.Path", lambda *a, **kw: tmp_path / "dynamic_tools"):
            fresh_registry.register_dynamic("dyn1", simple_tool_code, "A dynamic", tags=["test"])
        all_tools = fresh_registry.describe_all_tools()
        dynamic_names = [t["name"] for t in all_tools if t.get("dynamic")]
        assert "dyn1" in dynamic_names


# ===========================================================================
# 2. ToolRegistry: persistence
# ===========================================================================

class TestToolRegistryPersistence:

    def test_save_dynamic_tool(self, fresh_registry, simple_tool_code, tmp_path):
        """save_dynamic_tool writes a JSON file to the given directory."""
        persist_dir = tmp_path / "dyn"
        fresh_registry.register_dynamic(
            "save_me", simple_tool_code, "Save test",
            input_schema={"value": "int"}, tags=["demo"]
        )
        ok = fresh_registry.save_dynamic_tool("save_me", persist_dir)
        assert ok is True
        saved = (persist_dir / "save_me.json")
        assert saved.exists()
        data = json.loads(saved.read_text())
        assert data["name"] == "save_me"
        assert "run_tool" in data["code"]
        assert data["input_schema"] == {"value": "int"}

    def test_load_persisted_tools(self, fresh_registry, simple_tool_code, tmp_path):
        """load_persisted_tools reloads saved tools into a fresh registry."""
        # Save
        persist_dir = tmp_path / "dyn"
        fresh_registry.register_dynamic("reload_me", simple_tool_code, "Reload test")
        fresh_registry.save_dynamic_tool("reload_me", persist_dir)

        # Load into new registry
        from tools.base_tool import ToolRegistry
        new_reg = ToolRegistry()
        n = new_reg.load_persisted_tools(persist_dir)
        assert n == 1
        assert new_reg.tool_exists("reload_me") is True

    def test_load_persisted_empty_dir(self, fresh_registry, tmp_path):
        """load_persisted_tools on an empty dir returns 0."""
        n = fresh_registry.load_persisted_tools(tmp_path / "empty")
        assert n == 0


# ===========================================================================
# 3. tool_persistence module
# ===========================================================================

class TestToolPersistenceModule:

    def test_persist_and_load(self, tmp_path, monkeypatch, simple_tool_code):
        import tools.tool_persistence as tp
        monkeypatch.setattr(tp, "_PERSIST_DIR", tmp_path / "dyn")

        ok = tp.persist_tool(
            name="mod_tool",
            code=simple_tool_code,
            description="Module persistence test",
            tags=["test"],
        )
        assert ok is True
        assert "mod_tool" in tp.list_persisted()

        records = tp.load_all()
        assert any(r["name"] == "mod_tool" for r in records)

    def test_delete_persisted(self, tmp_path, monkeypatch, simple_tool_code):
        import tools.tool_persistence as tp
        monkeypatch.setattr(tp, "_PERSIST_DIR", tmp_path / "dyn")
        tp.persist_tool("del_tool", simple_tool_code, "delete me")
        deleted = tp.delete_persisted("del_tool")
        assert deleted is True
        assert "del_tool" not in tp.list_persisted()


# ===========================================================================
# 4. sandbox_tester
# ===========================================================================

class TestSandboxTester:

    @pytest.mark.asyncio
    async def test_safe_code_passes(self, simple_tool_code):
        from tools.sandbox_tester import sandbox_test_tool
        passed, output = await sandbox_test_tool(simple_tool_code, {}, timeout=15)
        assert passed is True

    @pytest.mark.asyncio
    async def test_broken_code_fails(self, broken_tool_code):
        from tools.sandbox_tester import sandbox_test_tool
        passed, output = await sandbox_test_tool(broken_tool_code, {}, timeout=15)
        assert passed is False
        assert "intentional" in output.lower() or "error" in output.lower()

    @pytest.mark.asyncio
    async def test_timeout_enforced(self):
        slow_code = """
import time
def run_tool(input_data):
    time.sleep(30)
    return {"success": True, "result": None, "error": None}
"""
        from tools.sandbox_tester import sandbox_test_tool
        passed, output = await sandbox_test_tool(slow_code, {}, timeout=2)
        assert passed is False
        assert "timeout" in output.lower()

    @pytest.mark.asyncio
    async def test_no_runtool_fails(self, no_runtool_code):
        from tools.sandbox_tester import sandbox_test_tool
        passed, output = await sandbox_test_tool(no_runtool_code, {}, timeout=10)
        assert passed is False


# ===========================================================================
# 5. BaseAgent tool helpers
# ===========================================================================

class TestBaseAgentToolHelpers:

    @pytest.mark.asyncio
    async def test_tool_exists_helper(self):
        """BaseAgent.tool_exists() delegates to ToolRegistry.tool_exists()."""
        from agents.message_bus import MessageBus
        from agents.coordinator_agent import CoordinatorAgent
        from tools.base_tool import registry

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        agent = CoordinatorAgent(mock_bus)

        # Use the real registry
        assert isinstance(agent.tool_exists("system_monitor"), bool)

    @pytest.mark.asyncio
    async def test_list_available_tools_helper(self):
        """BaseAgent.list_available_tools() returns a list of dicts."""
        from agents.message_bus import MessageBus
        from agents.coordinator_agent import CoordinatorAgent

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        agent = CoordinatorAgent(mock_bus)
        tools = agent.list_available_tools()
        assert isinstance(tools, list)
        # Each entry should have name and description
        for t in tools:
            assert "name" in t

    @pytest.mark.asyncio
    async def test_call_tool_helper(self, fresh_registry, simple_tool_code, tmp_path):
        """BaseAgent.call_tool() uses the real registry."""
        from agents.message_bus import MessageBus
        from agents.coordinator_agent import CoordinatorAgent

        # Pre-register a dynamic tool in the module-level registry
        from tools.base_tool import registry
        with patch("tools.base_tool.Path", lambda *a, **kw: tmp_path / "dyn"):
            registry.register_dynamic("agent_call_test", simple_tool_code, "Test")

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        agent = CoordinatorAgent(mock_bus)

        result = await agent.call_tool("agent_call_test", value=7)
        assert result["success"] is True
        assert result["result"] == 7


# ===========================================================================
# 6. SecurityAgent: security.test_tool handler
# ===========================================================================

class TestSecurityAgentTestTool:

    @pytest.mark.asyncio
    async def test_handle_test_tool_passes(self, simple_tool_code):
        """A syntactically correct, safe tool should pass the security check."""
        from agents.message_bus import MessageBus, BusMessage
        from agents.security_agent import SecurityAnalysisAgent

        published = []

        async def mock_publish(msg: BusMessage):
            published.append(msg)

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        mock_bus.publish = mock_publish

        agent = SecurityAnalysisAgent(mock_bus)

        msg = BusMessage(
            topic="security.test_tool",
            sender="developer",
            payload={"code": simple_tool_code, "tool_name": "safe_tool"},
            reply_to="developer.sec_result.safe_tool",
        )
        await agent._handle_test_tool(msg)

        assert len(published) == 1
        result = published[0].payload
        assert result["tool_name"] == "safe_tool"
        assert result["passed"] is True
        assert result["score"] >= 40

    @pytest.mark.asyncio
    async def test_handle_test_tool_fails(self, broken_tool_code):
        """A tool that raises at runtime should fail the security check."""
        from agents.message_bus import MessageBus, BusMessage
        from agents.security_agent import SecurityAnalysisAgent

        published = []

        async def mock_publish(msg: BusMessage):
            published.append(msg)

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        mock_bus.publish = mock_publish

        agent = SecurityAnalysisAgent(mock_bus)

        msg = BusMessage(
            topic="security.test_tool",
            sender="developer",
            payload={"code": broken_tool_code, "tool_name": "broken_tool"},
            reply_to="developer.sec_result.broken_tool",
        )
        await agent._handle_test_tool(msg)

        assert len(published) == 1
        result = published[0].payload
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_handle_test_tool_empty_code(self):
        """Empty code payload should return passed=False without crashing."""
        from agents.message_bus import MessageBus, BusMessage
        from agents.security_agent import SecurityAnalysisAgent

        published = []

        async def mock_publish(msg: BusMessage):
            published.append(msg)

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        mock_bus.publish = mock_publish

        agent = SecurityAnalysisAgent(mock_bus)
        msg = BusMessage(
            topic="security.test_tool",
            sender="developer",
            payload={"code": "", "tool_name": "empty"},
            reply_to="test.reply",
        )
        await agent._handle_test_tool(msg)
        assert published[0].payload["passed"] is False


# ===========================================================================
# 7. MemoryAgent: memory.store_tool handler
# ===========================================================================

class TestMemoryAgentStoreTool:

    @pytest.mark.asyncio
    async def test_handle_store_tool(self):
        """store_tool must call vector_store.add() and knowledge_db.insert_analysis()."""
        from agents.message_bus import MessageBus, BusMessage
        from agents.memory_agent import MemoryAgent

        mock_bus   = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()

        mock_vs = MagicMock()
        mock_vs.add = MagicMock(return_value="vec123")

        mock_db = AsyncMock()
        mock_db.insert_analysis = AsyncMock()

        mock_conv = MagicMock()

        agent = MemoryAgent(mock_bus, mock_db, mock_vs, mock_conv)

        msg = BusMessage(
            topic="memory.store_tool",
            sender="developer",
            payload={
                "name": "my_tool",
                "description": "Does something",
                "input_schema": {"x": "int"},
                "output_schema": {"result": "int"},
                "tags": ["test"],
                "version": "1.0.0",
                "author_agent": "developer",
                "test_passed": True,
                "test_output": "[1, 2, 3]",
                "code_hash": "abc123",
            },
        )
        await agent._handle_store_tool(msg)

        mock_vs.add.assert_called_once()
        # Check the text contains the tool name
        call_text = mock_vs.add.call_args[0][0]
        assert "my_tool" in call_text

        mock_db.insert_analysis.assert_called_once()
        call_kwargs = mock_db.insert_analysis.call_args.kwargs
        assert call_kwargs["target"] == "tool:my_tool"
        assert call_kwargs["result_type"] == "dynamic_tool"


# ===========================================================================
# 8. PlannerAgent: missing tool detection
# ===========================================================================

class TestPlannerMissingToolDetection:

    @pytest.mark.asyncio
    async def test_no_missing_tools_when_all_present(self):
        """If all required tools already exist, _ensure_required_tools returns []."""
        from agents.message_bus import MessageBus
        from agents.planner_agent import PlannerAgent

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        agent = PlannerAgent(mock_bus)

        # Mock tool_exists to return True for everything
        agent.tool_exists = MagicMock(return_value=True)
        created = await agent._ensure_required_tools(
            "port scan the host", "port scan host"
        )
        assert created == []

    @pytest.mark.asyncio
    async def test_missing_tool_triggers_generation(self):
        """_ensure_required_tools publishes developer.request when a tool is missing."""
        from agents.message_bus import MessageBus, BusMessage
        from agents.planner_agent import PlannerAgent

        published = []

        async def mock_publish(msg: BusMessage):
            published.append(msg)
            # Simulate DeveloperAgent reply so the future resolves
            if msg.reply_to:
                reply = BusMessage(
                    topic=msg.reply_to,
                    sender="developer",
                    payload={"result": "✅ Tool 'port_scanner' generated."},
                )
                # Trigger all subscribers for this reply topic
                for sub_topic, handler in mock_bus._subs.items():
                    if sub_topic == msg.reply_to:
                        asyncio.ensure_future(handler(reply))

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus._subs: dict = {}
        mock_bus.publish = mock_publish

        def mock_subscribe(topic, handler):
            mock_bus._subs[topic] = handler

        mock_bus.subscribe = mock_subscribe

        agent = PlannerAgent(mock_bus)
        agent.tool_exists = MagicMock(return_value=False)  # Everything is "missing"

        created = await asyncio.wait_for(
            agent._ensure_required_tools(
                "- [1] port scan the target host", "scan ports"
            ),
            timeout=5.0,
        )
        # Should have attempted to publish a generate_tool request
        assert any(m.topic == "developer.request" for m in published)

    def test_capability_tool_map_completeness(self):
        """_CAPABILITY_TOOL_MAP is a non-empty dict with string keys and values."""
        from agents.planner_agent import _CAPABILITY_TOOL_MAP
        assert isinstance(_CAPABILITY_TOOL_MAP, dict)
        assert len(_CAPABILITY_TOOL_MAP) > 5
        for k, v in _CAPABILITY_TOOL_MAP.items():
            assert isinstance(k, str) and isinstance(v, str)


# ===========================================================================
# 9. DeveloperAgent: bus subscriptions and routing
# ===========================================================================

class TestDeveloperAgentBusRouting:

    def test_subscriptions_registered(self):
        """DeveloperAgent subscribes to developer.*, developer.create_agent, developer.request."""
        from agents.message_bus import MessageBus
        from agents.developer_agent import DeveloperAgent

        subscribed: list = []

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = lambda topic, _: subscribed.append(topic)

        agent = DeveloperAgent(mock_bus)
        asyncio.get_event_loop().run_until_complete(agent._register_subscriptions())

        assert "developer.*" in subscribed
        assert "developer.create_agent" in subscribed
        assert "developer.request" in subscribed

    @pytest.mark.asyncio
    async def test_generate_tool_action_routes_correctly(self):
        """Action='generate_tool' should call _generate_tool."""
        from agents.message_bus import MessageBus, BusMessage
        from agents.developer_agent import DeveloperAgent

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()
        mock_bus.publish = AsyncMock()

        agent = DeveloperAgent(mock_bus)
        agent._generate_tool = AsyncMock(return_value="✅ Tool created")

        msg = BusMessage(
            topic="developer.request",
            sender="planner",
            payload={"action": "generate_tool", "task": "make a tool"},
            reply_to="planner.tool_reply",
        )
        await agent._handle_dev_request(msg)
        agent._generate_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_task_returns_error(self):
        """Missing task field in payload should return a validation error."""
        from agents.message_bus import MessageBus, BusMessage
        from agents.developer_agent import DeveloperAgent

        published = []

        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.subscribe = MagicMock()

        async def mock_publish(msg):
            published.append(msg)

        mock_bus.publish = mock_publish

        agent = DeveloperAgent(mock_bus)
        msg = BusMessage(
            topic="developer.request",
            sender="planner",
            payload={"action": "generate_tool"},  # no "task"
            reply_to="test.reply",
        )
        await agent._handle_dev_request(msg)
        assert any("error" in m.payload for m in published)
