"""
TASO – Coder Agent

Specialized agent for code generation, refactoring, and code review.
Uses the model router to select the best coding model.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from models.model_router import router as model_router
from models.model_registry import TaskType

log = get_logger("agent")

_CODER_SYSTEM = """You are an expert software engineer with deep knowledge of Python, security,
and systems programming. Write clean, well-documented, production-ready code.
Always include error handling. Prefer async/await where appropriate."""


class CoderAgent(BaseAgent):
    name        = "coder"
    description = "Writes and refactors code. Routes to coding-specialist models."

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("coder.*", self._handle_code_request)

    async def _handle_code_request(self, msg: BusMessage) -> None:
        task = msg.payload.get("task", "")
        context = msg.payload.get("context", "")
        if not task:
            return

        result = await self.handle(task, context)
        if msg.reply_to:
            await self.publish(
                topic=msg.reply_to,
                payload={"result": result, "agent": self.name},
                recipient=msg.sender,
            )

    async def handle(self, description: str, context: str = "") -> str:
        """Generate or refactor code for the given task."""
        prompt = description
        if context:
            prompt = f"{context}\n\nTask: {description}"

        return await model_router.query(
            prompt=prompt,
            system=_CODER_SYSTEM,
            task_type=TaskType.CODING,
        )
