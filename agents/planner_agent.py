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

log = get_logger("agent")


class PlannerAgent(BaseAgent):
    name        = "planner"
    description = "Decomposes complex requests into subtasks and coordinates agent swarm execution."

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("planner.*", self._handle_plan_request)

    async def _handle_plan_request(self, msg: BusMessage) -> None:
        request = msg.payload.get("request", "")
        if not request:
            return

        self._log.info(f"PlannerAgent: planning '{request[:80]}'")

        from swarm.task_planner import task_planner
        plan = await task_planner.plan(request, msg.payload.get("context", {}))

        subtasks = [
            {
                "id": t.id,
                "description": t.description,
                "capability": t.capability,
                "depends_on": t.depends_on,
                "priority": t.priority,
            }
            for t in plan.subtasks
        ]

        await self.publish(
            topic=msg.reply_to or "coordinator.plan_ready",
            payload={"plan": subtasks, "request": request},
            recipient=msg.sender,
        )

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use."""
        from swarm.task_planner import task_planner
        plan = await task_planner.plan(description)
        return "\n".join(f"- [{t.id}] {t.description}" for t in plan.subtasks)
