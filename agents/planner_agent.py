"""
TASO – Planner Agent

Uses the swarm task planner to break requests into subtasks,
then delegates to appropriate agents via the bus.

Enhanced with missing-tool detection: if the generated plan requires
capabilities that map to unregistered tools, PlannerAgent requests
DeveloperAgent to generate them before returning the plan.
"""
from __future__ import annotations
import asyncio
import re
from typing import Any, Dict, List, Optional
from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from swarm.task_planner import task_planner

log = get_logger("agent")

# Map of capability keywords → expected tool names.
# If a plan mentions a capability and the tool isn't registered, it will be
# generated automatically.
_CAPABILITY_TOOL_MAP: Dict[str, str] = {
    "port scan":        "port_scanner",
    "port_scan":        "port_scanner",
    "network scan":     "network_check",
    "dns lookup":       "dns_lookup",
    "whois":            "whois_lookup",
    "hash file":        "file_hasher",
    "file hash":        "file_hasher",
    "encode base64":    "base64_encoder",
    "decode base64":    "base64_encoder",
    "http request":     "http_client",
    "fetch url":        "http_client",
    "parse json":       "json_parser",
    "diff files":       "file_differ",
    "compress":         "file_compressor",
    "extract archive":  "archive_extractor",
}


class PlannerAgent(BaseAgent):
    name = "planner"
    description = "Decomposes complex requests into subtasks and coordinates agent swarm execution."

    async def _register_subscriptions(self) -> None:
        """Registers the subscriptions for the planner agent."""
        self._bus.subscribe("planner.*", self._handle_plan_request)

    async def _handle_plan_request(self, msg: BusMessage) -> None:
        """Handles incoming planning requests."""
        request = msg.payload.get("request")
        if not request:
            self._log.error("Received a planning request without a 'request' field.")
            await self._send_error_response(
                msg, "Missing 'request' field in the payload."
            )
            return

        self._log.info(f"PlannerAgent: planning '{request[:80]}'")

        try:
            context = msg.payload.get("context", {})
            plan = await task_planner.plan(request, context)
        except Exception as e:
            self._log.exception("Failed to generate plan.")
            await self._send_error_response(
                msg, f"Error generating plan: {str(e)}"
            )
            return

        # Auto-provision any missing tools referenced in the plan
        plan_text = self._format_plan_for_display(plan.subtasks)
        created_tools = await self._ensure_required_tools(plan_text, request)

        subtasks = self._format_subtasks(plan.subtasks)

        try:
            payload: Dict[str, Any] = {"plan": subtasks, "request": request}
            if created_tools:
                payload["auto_created_tools"] = created_tools
            await self.publish(
                topic=msg.reply_to or "coordinator.plan_ready",
                payload=payload,
                recipient=msg.sender,
            )
        except Exception:
            self._log.exception("Failed to publish the plan.")

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use."""
        try:
            plan = await task_planner.plan(description, context)
        except Exception as e:
            self._log.exception("Failed to generate plan via handle method.")
            return f"Error generating plan: {e}"

        plan_text = self._format_plan_for_display(plan.subtasks)
        created = await self._ensure_required_tools(plan_text, description)
        result   = plan_text
        if created:
            result += f"\n\n🔧 Auto-created tools: {', '.join(created)}"
        return result

    # ------------------------------------------------------------------
    # Missing-tool detection + auto-provisioning
    # ------------------------------------------------------------------

    async def _ensure_required_tools(
        self, plan_text: str, original_request: str
    ) -> List[str]:
        """
        Scan plan_text for capability keywords that map to specific tools.
        For each missing tool, request DeveloperAgent to generate it via
        the bus.  Returns list of tool names that were successfully created.
        """
        needed: Dict[str, str] = {}  # tool_name → capability description

        lower = plan_text.lower() + " " + original_request.lower()
        for keyword, tool_name in _CAPABILITY_TOOL_MAP.items():
            if keyword in lower and not self.tool_exists(tool_name):
                needed[tool_name] = keyword

        if not needed:
            return []

        self._log.info(
            f"PlannerAgent: missing tools detected: {list(needed.keys())}"
        )
        created: List[str] = []

        for tool_name, capability in needed.items():
            try:
                reply_topic = f"planner.tool_created.{tool_name}"
                fut: asyncio.Future = asyncio.get_event_loop().create_future()

                def _on_reply(m: BusMessage, _f=fut) -> None:
                    if not _f.done():
                        _f.set_result(m.payload)

                self._bus.subscribe(reply_topic, lambda m: _on_reply(m))

                await self.publish(
                    topic="developer.request",
                    payload={
                        "action":    "generate_tool",
                        "task":      f"A tool that performs: {capability}. "
                                     f"Tool name should be '{tool_name}'.",
                        "tool_name": tool_name,
                    },
                    reply_to=reply_topic,
                )

                result = await asyncio.wait_for(fut, timeout=90)
                if "error" not in (result.get("result", "") or "").lower()[:20]:
                    created.append(tool_name)
                    self._log.info(
                        f"PlannerAgent: auto-created tool '{tool_name}'"
                    )
            except asyncio.TimeoutError:
                self._log.warning(
                    f"PlannerAgent: timed out waiting for tool '{tool_name}' creation"
                )
            except Exception as exc:
                self._log.warning(
                    f"PlannerAgent: failed to create tool '{tool_name}': {exc}"
                )

        return created

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_subtasks(subtasks: list) -> list:
        """Formats subtasks into a dictionary representation."""
        try:
            return [
                {
                    "id": t.id,
                    "description": t.description,
                    "capability": t.capability,
                    "depends_on": t.depends_on,
                    "priority": t.priority,
                }
                for t in subtasks
            ]
        except AttributeError as e:
            log.exception("Error formatting subtasks: Invalid subtask structure.")
            raise ValueError("Invalid subtask structure.") from e

    @staticmethod
    def _format_plan_for_display(subtasks: list) -> str:
        """Formats the plan for display as a string."""
        try:
            return "\n".join(f"- [{t.id}] {t.description}" for t in subtasks)
        except AttributeError:
            log.exception("Error formatting plan for display: Invalid subtask structure.")
            return "Error: Invalid subtask structure."

    async def _send_error_response(self, msg: BusMessage, error_message: str) -> None:
        """Sends an error response back to the sender."""
        try:
            await self.publish(
                topic=msg.reply_to or "coordinator.plan_failed",
                payload={"error": error_message, "request": msg.payload.get("request")},
                recipient=msg.sender,
            )
        except Exception:
            self._log.exception("Failed to send error response.")
