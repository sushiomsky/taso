"""
TASO – tools/tool_registry.py

Compatibility re-export so code can do::

    from tools.tool_registry import ToolRegistry, registry

The canonical implementation lives in tools/base_tool.py alongside
ToolSchema and BaseTool; this module simply re-exports it.
"""
from tools.base_tool import ToolRegistry, ToolSchema, BaseTool, registry  # noqa: F401

__all__ = ["ToolRegistry", "ToolSchema", "BaseTool", "registry"]
