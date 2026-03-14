"""
TASO – Analysis Agent

Evaluates information, synthesizes research findings,
and produces structured analysis reports.
"""
from __future__ import annotations
from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from models.model_router import router as model_router
from models.model_registry import TaskType

log = get_logger("agent")

_ANALYSIS_SYSTEM = """You are a senior security researcher and systems analyst.
Produce clear, structured analysis reports with:
  - Executive summary
  - Key findings (bullet points)
  - Risk assessment (if applicable)
  - Recommendations
Be precise and evidence-based."""


class AnalysisAgent(BaseAgent):
    name        = "analysis"
    description = "Evaluates and synthesizes information. Produces structured analysis reports."

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("analysis.*", self._handle_analysis_request)

    async def _handle_analysis_request(self, msg: BusMessage) -> None:
        data = msg.payload.get("data", "")
        task = msg.payload.get("task", "Analyze the following.")
        if not data:
            return

        result = await self.handle(f"{task}\n\n{data}", "")
        if msg.reply_to:
            await self.publish(
                topic=msg.reply_to,
                payload={"result": result, "agent": self.name},
                recipient=msg.sender,
            )

    async def handle(self, description: str, context: str = "") -> str:
        prompt = description
        if context:
            prompt = f"{context}\n\n{description}"

        return await model_router.query(
            prompt=prompt,
            system=_ANALYSIS_SYSTEM,
            task_type=TaskType.ANALYSIS,
        )
