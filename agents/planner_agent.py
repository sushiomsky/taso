"""
TASO – Planner Agent

Uses the swarm task planner to break requests into subtasks,
then delegates to appropriate agents via the bus.
"""
from __future__ import annotations
from typing import Any, Dict
from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from swarm.task_planner import task_planner

log = get_logger("agent")


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

        subtasks = self._format_subtasks(plan.subtasks)

        try:
            await self.publish(
                topic=msg.reply_to or "coordinator.plan_ready",
                payload={"plan": subtasks, "request": request},
                recipient=msg.sender,
            )
        except Exception as e:
            self._log.exception("Failed to publish the plan.")
            # Log the error but do not re-raise to avoid crashing the agent.

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use."""
        try:
            plan = await task_planner.plan(description, context)
        except Exception as e:
            self._log.exception("Failed to generate plan via handle method.")
            return f"Error generating plan: {e}"

        return self._format_plan_for_display(plan.subtasks)

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
        except AttributeError as e:
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
        except Exception as e:
            self._log.exception("Failed to send error response.")
            # Log the error but do not re-raise to avoid crashing the agent.
