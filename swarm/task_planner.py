"""
TASO – Task Planner

Uses the LLM to decompose complex user requests into subtasks,
each assigned to a specific agent capability.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from config.logging_config import get_logger
from models.model_router import router as model_router
from models.model_registry import TaskType

log = get_logger("task_planner")

_PLANNER_SYSTEM = """You are a task decomposition engine for an autonomous AI security research platform.

When given a user request, break it into concrete subtasks.
Each subtask must specify:
- id: short snake_case identifier
- description: what to do (1-2 sentences)
- capability: one of: coding, analysis, security, research, planning, general
- depends_on: list of subtask ids this depends on (empty = can run immediately)
- priority: 1 (high) to 3 (low)

Return ONLY a JSON array of subtask objects. No explanation before or after.

Example:
[
  {"id": "research_vulns", "description": "Search for CVEs related to the target", "capability": "security", "depends_on": [], "priority": 1},
  {"id": "analyze_code", "description": "Analyze the provided code for vulnerabilities", "capability": "coding", "depends_on": ["research_vulns"], "priority": 1}
]"""


@dataclass
class SubTask:
    id: str
    description: str
    capability: str
    depends_on: List[str] = field(default_factory=list)
    priority: int = 2
    result: Optional[str] = None
    status: str = "pending"   # pending | running | done | failed


@dataclass
class TaskPlan:
    original_request: str
    subtasks: List[SubTask]
    context: Dict[str, Any] = field(default_factory=dict)

    def ready_tasks(self) -> List[SubTask]:
        """Return subtasks whose dependencies are all done."""
        done_ids = {t.id for t in self.subtasks if t.status == "done"}
        return [
            t for t in self.subtasks
            if t.status == "pending"
            and all(dep in done_ids for dep in t.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(t.status in ("done", "failed") for t in self.subtasks)


class TaskPlanner:
    """LLM-powered task decomposition."""

    async def plan(self, request: str, context: Dict[str, Any] = None) -> TaskPlan:
        """
        Decompose a user request into a TaskPlan with ordered SubTasks.
        Falls back to a single general task if LLM fails.
        """
        context = context or {}
        subtasks = await self._llm_decompose(request) or self._fallback_plan(request)
        plan = TaskPlan(original_request=request, subtasks=subtasks, context=context)
        log.info(f"TaskPlanner: decomposed into {len(subtasks)} subtasks.")
        return plan

    async def _llm_decompose(self, request: str) -> Optional[List[SubTask]]:
        try:
            response = await model_router.query(
                prompt=f"User request: {request}",
                system=_PLANNER_SYSTEM,
                task_type=TaskType.PLANNING,
            )
            # Extract JSON from response
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if not json_match:
                return None
            raw = json.loads(json_match.group())
            return [
                SubTask(
                    id=t.get("id", f"task_{i}"),
                    description=t.get("description", ""),
                    capability=t.get("capability", "general"),
                    depends_on=t.get("depends_on", []),
                    priority=t.get("priority", 2),
                )
                for i, t in enumerate(raw)
            ]
        except Exception as exc:
            log.warning(f"TaskPlanner: LLM decompose failed: {exc}")
            return None

    def _fallback_plan(self, request: str) -> List[SubTask]:
        """Single-task fallback when LLM decomposition fails."""
        return [SubTask(
            id="main_task",
            description=request,
            capability="general",
            depends_on=[],
            priority=1,
        )]


task_planner = TaskPlanner()
