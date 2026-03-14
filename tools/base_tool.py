"""
TASO – Tool base class + registry.

Every tool in tools/ must subclass BaseTool and be discoverable by
the ToolRegistry which auto-imports all modules in this package.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from config.logging_config import tool_log as log


class ToolSchema:
    """Describes a tool's input schema."""

    def __init__(self, fields: Dict[str, Dict]) -> None:
        """
        fields example:
          {
            "repo_path": {"type": "str", "required": True,  "description": "..."},
            "depth":     {"type": "int", "required": False, "default": 3},
          }
        """
        self.fields = fields

    def validate(self, inputs: Dict[str, Any]) -> Optional[str]:
        """Return an error string if validation fails, else None."""
        for name, spec in self.fields.items():
            if spec.get("required") and name not in inputs:
                return f"Missing required field: '{name}'"
            if name in inputs and not isinstance(inputs[name], eval(spec.get("type", "str"))):
                return f"Invalid type for field: '{name}', expected {spec.get('type')}"
        return None


class BaseTool(ABC):
    """Abstract base class for all TASO tools."""

    name: str = "base_tool"
    description: str = ""
    schema: ToolSchema = ToolSchema({})

    async def run(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Validate inputs then call execute().
        Returns a dict with at least {"success": bool, "result": ..., "error": ...}.
        """
        error = self.schema.validate(kwargs)
        if error:
            log.warning(f"Validation failed for tool '{self.name}': {error}")
            return {"success": False, "result": None, "error": error}

        try:
            result = await self.execute(**kwargs)
            return {"success": True, "result": result, "error": None}
        except Exception as exc:
            log.error(f"Tool '{self.name}' execution error: {exc}", exc_info=True)
            return {"success": False, "result": None, "error": f"An error occurred: {str(exc)}"}

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Implement the tool's core logic."""

    def describe(self) -> Dict[str, Any]:
        return {
            "name":        self.name,
            "description": self.description,
            "schema":      self.schema.fields,
        }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Automatically discovers and registers all BaseTool subclasses
    defined in the tools/ package.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._dynamic_tools: Dict[str, Any] = {}

    def discover(self) -> None:
        """Import all modules in the tools package and register tools."""
        tools_pkg_path = Path(__file__).parent

        for module_info in pkgutil.iter_modules([str(tools_pkg_path)]):
            if module_info.name.startswith("_"):
                continue
            try:
                mod = importlib.import_module(f"tools.{module_info.name}")
            except Exception as exc:
                log.warning(f"Could not import tools.{module_info.name}: {exc}", exc_info=True)
                continue

            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseTool)
                    and obj is not BaseTool
                    and obj.name != "base_tool"
                ):
                    try:
                        instance = obj()
                        self._tools[instance.name] = instance
                        log.debug(f"Registered tool: {instance.name}")
                    except Exception as exc:
                        log.error(f"Failed to instantiate tool '{obj.__name__}': {exc}", exc_info=True)

        log.info(f"ToolRegistry: {len(self._tools)} tools loaded.")

    def get(self, name: str) -> Optional[BaseTool]:
        tool = self._tools.get(name)
        if not tool:
            log.warning(f"Tool '{name}' not found in registry.")
        return tool

    def list_tools(self) -> List[Dict[str, Any]]:
        return [t.describe() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def register_dynamic(self, name: str, code: str, description: str,
                          input_schema: Dict = None, output_schema: Dict = None,
                          tags: List[str] = None, version: str = "1.0.0") -> bool:
        """
        Register a dynamically generated tool from raw code string.
        The code must define run_tool(input_data: dict) -> dict.
        Returns True if registration succeeded.
        """
        import types
        import asyncio
        try:
            module = types.ModuleType(f"dynamic_tool_{name}")
            exec(compile(code, f"<dynamic:{name}>", "exec"), module.__dict__)
            if not hasattr(module, "run_tool"):
                raise ValueError("Code does not define run_tool()")

            raw_fn = module.run_tool
            if asyncio.iscoroutinefunction(raw_fn):
                async_fn = raw_fn
            else:
                async def async_fn(input_data, _fn=raw_fn):
                    return await asyncio.get_event_loop().run_in_executor(None, _fn, input_data)

            async_fn.__name__       = f"run_tool_{name}"
            async_fn.__doc__        = description
            async_fn._dynamic       = True
            async_fn._code          = code
            async_fn._version       = version
            async_fn._input_schema  = input_schema or {}
            async_fn._output_schema = output_schema or {}
            async_fn._tags          = tags or ["dynamic"]

            self._dynamic_tools[name] = async_fn
            log.info(f"ToolRegistry: dynamic tool '{name}' registered (v{version})")
            return True
        except Exception as exc:
            log.error(f"ToolRegistry: failed to register dynamic tool '{name}': {exc}", exc_info=True)
            return False

    def get_dynamic(self, name: str):
        dynamic_tool = self._dynamic_tools.get(name)
        if not dynamic_tool:
            log.warning(f"Dynamic tool '{name}' not found in registry.")
        return dynamic_tool

    def list_dynamic(self) -> List[str]:
        return list(self._dynamic_tools.keys())


# Module-level singleton
registry = ToolRegistry()
