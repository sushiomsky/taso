"""
Tests for the Tool Registry and BaseTool infrastructure.

Covers:
  - ToolSchema validation (required fields, type checking)
  - Tool discovery (ToolRegistry.discover)
  - Tool execution (run returns expected dict shape)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.base_tool import BaseTool, ToolRegistry, ToolSchema


# ---------------------------------------------------------------------------
# Minimal concrete tools for testing
# ---------------------------------------------------------------------------

class EchoTool(BaseTool):
    name = "echo"
    description = "Echoes input back"
    schema = ToolSchema({
        "message": {"type": "str", "required": True,  "description": "Text to echo"},
        "repeat":  {"type": "int", "required": False, "default": 1},
    })

    async def execute(self, **kwargs: Any) -> Any:
        # run() wraps this in {"success": True, "result": <this>, "error": None}
        return kwargs["message"] * kwargs.get("repeat", 1)


class FailTool(BaseTool):
    name = "fail"
    description = "Always raises"
    schema = ToolSchema({})

    async def execute(self, **kwargs: Any) -> Any:
        raise RuntimeError("deliberate failure")


# ---------------------------------------------------------------------------
# ToolSchema tests
# ---------------------------------------------------------------------------

def test_schema_valid_input():
    schema = ToolSchema({"path": {"type": "str", "required": True, "description": "fp"}})
    assert schema.validate({"path": "/tmp/x"}) is None


def test_schema_missing_required():
    schema = ToolSchema({"path": {"type": "str", "required": True, "description": "fp"}})
    err = schema.validate({})
    assert err is not None and "path" in err


def test_schema_wrong_type():
    schema = ToolSchema({"count": {"type": "int", "required": True, "description": "n"}})
    assert schema.validate({"count": "not_an_int"}) is not None


def test_schema_optional_missing_ok():
    schema = ToolSchema({"opt": {"type": "str", "required": False}})
    assert schema.validate({}) is None


# ---------------------------------------------------------------------------
# BaseTool / run() tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_echo_tool_success():
    result = await EchoTool().run(message="hello")
    assert result["success"] is True
    assert result["result"] == "hello"


@pytest.mark.asyncio
async def test_echo_tool_missing_required():
    result = await EchoTool().run()  # no message
    assert result["success"] is False
    assert result.get("error")


@pytest.mark.asyncio
async def test_fail_tool_returns_error():
    result = await FailTool().run()
    assert result["success"] is False
    assert "deliberate failure" in result.get("error", "")


# ---------------------------------------------------------------------------
# ToolRegistry tests
# ---------------------------------------------------------------------------

def test_registry_discover_loads_tools():
    reg = ToolRegistry()
    reg.discover()
    assert len(reg.list_tools()) >= 1


def test_registry_get_known_tool():
    reg = ToolRegistry()
    reg.discover()
    tool = reg.get("system_monitor")
    assert tool is not None
    assert isinstance(tool, BaseTool)


def test_registry_get_unknown_returns_none():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("nonexistent_xyz") is None


def test_registry_contains():
    reg = ToolRegistry()
    reg.discover()
    tools = reg.list_tools()
    if tools:
        assert tools[0]["name"] in reg
