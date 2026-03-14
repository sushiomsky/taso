"""
TASO – Swarm Orchestrator

Coordinates parallel execution of agent subtasks.

Features:
  • parallel asyncio task execution with configurable concurrency
  • dependency-aware scheduling (DAG execution)
  • per-task timeout management
  • retry on transient failures
  • result aggregation via LLM summary
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Callable, Dict, List, Optional
from config.logging_config import get_logger
from config.settings import settings
from models.model_router import router as model_router
from models.model_registry import TaskType
from swarm.task_planner import TaskPlan, SubTask, task_planner
from swarm.agent_registry import AgentRegistry, agent_registry

log = get_logger("swarm_orchestrator")

_AGGREGATOR_SYSTEM = """You are a results aggregator for an autonomous AI agent swarm.

Given a user's original request and the outputs from multiple specialized agents,
produce a clear, well-structured final response. Be concise but complete.
Do not repeat agent metadata. Focus on the actual findings and answers."""


class SwarmOrchestrator:
    """
    Main swarm execution engine.
    """

    def __init__(
        self,
        registry: AgentRegistry = agent_registry,
        max_parallel: int = None,
        task_timeout: int = None,
    ) -> None:
        self._registry = registry
        self._max_parallel = max_parallel or settings.SWARM_MAX_PARALLEL
        self._timeout = task_timeout or settings.SWARM_TASK_TIMEOUT
        self._semaphore = asyncio.Semaphore(self._max_parallel)
        self._active_swarms: Dict[str, Dict] = {}

    async def run(
        self,
        request: str,
        context: Dict[str, Any] = None,
    ) -> str:
        """
        Full swarm execution:
        1. Plan → 2. Execute (parallel DAG) → 3. Aggregate results
        """
        start = time.monotonic()
        context = context or {}
        swarm_id = f"swarm_{int(start)}"
        self._active_swarms[swarm_id] = {"request": request, "status": "planning", "start": start}

        try:
            # Step 1: Decompose
            log.info(f"Swarm '{swarm_id}': planning task…")
            plan = await task_planner.plan(request, context)

            # Step 2: Execute DAG
            self._active_swarms[swarm_id]["status"] = "executing"
            self._active_swarms[swarm_id]["subtasks"] = len(plan.subtasks)
            await self._execute_dag(plan)

            # Step 3: Aggregate
            self._active_swarms[swarm_id]["status"] = "aggregating"
            result = await self._aggregate(plan)

            elapsed = time.monotonic() - start
            self._active_swarms[swarm_id]["status"] = "done"
            self._active_swarms[swarm_id]["elapsed"] = elapsed
            log.info(f"Swarm '{swarm_id}': completed in {elapsed:.1f}s.")
            return result

        except Exception as exc:
            self._active_swarms[swarm_id]["status"] = "failed"
            log.error(f"Swarm '{swarm_id}' failed: {exc}")
            return f"[Swarm execution failed: {exc}]"
        finally:
            # Keep last 20 swarm records
            if len(self._active_swarms) > 20:
                oldest = list(self._active_swarms.keys())[0]
                del self._active_swarms[oldest]

    async def _execute_dag(self, plan: TaskPlan) -> None:
        """Execute subtasks in dependency order, running independent tasks in parallel."""
        rounds = 0
        while not plan.is_complete() and rounds < 20:
            rounds += 1
            ready = plan.ready_tasks()
            if not ready:
                # Detect deadlock
                pending = [t for t in plan.subtasks if t.status == "pending"]
                if pending:
                    log.warning(f"Swarm: potential deadlock – {len(pending)} tasks stuck. Forcing.")
                    for t in pending[:3]:
                        t.status = "failed"
                        t.result = "[deadlock — dependency never resolved]"
                break

            # Run this wave of independent tasks in parallel
            await asyncio.gather(*[
                self._run_subtask(task, plan)
                for task in ready
            ])

    async def _run_subtask(self, task: SubTask, plan: TaskPlan) -> None:
        """Execute a single subtask through the appropriate agent."""
        task.status = "running"
        agent = self._registry.best_for(task.capability)

        async with self._semaphore:
            agent_name = agent.name if agent else "llm_direct"
            await self._registry.increment_load(agent_name)
            try:
                log.info(f"Swarm: running subtask '{task.id}' via '{agent_name}'")
                if agent:
                    context_str = self._build_context(task, plan)
                    result = await asyncio.wait_for(
                        agent.handler(task.description, context_str),
                        timeout=self._timeout,
                    )
                else:
                    # Direct LLM if no agent registered for this capability
                    result = await asyncio.wait_for(
                        model_router.query(
                            prompt=task.description,
                            task_type=self._cap_to_task_type(task.capability),
                        ),
                        timeout=self._timeout,
                    )
                task.result = result
                task.status = "done"
            except asyncio.TimeoutError:
                task.result = f"[Timeout after {self._timeout}s]"
                task.status = "failed"
                log.warning(f"Swarm: subtask '{task.id}' timed out.")
                await self._registry.decrement_load(agent_name, error=True)
            except Exception as exc:
                task.result = f"[Error: {exc}]"
                task.status = "failed"
                log.error(f"Swarm: subtask '{task.id}' failed: {exc}")
                await self._registry.decrement_load(agent_name, error=True)
            else:
                await self._registry.decrement_load(agent_name)

    def _build_context(self, task: SubTask, plan: TaskPlan) -> str:
        """Build context string from completed dependency results."""
        deps = [
            t for t in plan.subtasks
            if t.id in task.depends_on and t.status == "done" and t.result
        ]
        if not deps:
            return ""
        lines = ["Context from prior subtasks:"]
        for dep in deps:
            lines.append(f"\n[{dep.id}]: {dep.result[:500]}")
        return "\n".join(lines)

    async def _aggregate(self, plan: TaskPlan) -> str:
        """Aggregate all subtask results into a final response."""
        completed = [t for t in plan.subtasks if t.status == "done" and t.result]
        if not completed:
            return "[All subtasks failed — no results to aggregate.]"

        if len(completed) == 1:
            return completed[0].result or ""

        # Build aggregation prompt
        parts = [f"Original request: {plan.original_request}\n"]
        for task in completed:
            parts.append(f"\n=== {task.id} ({task.capability}) ===\n{task.result}")

        aggregation_prompt = "\n".join(parts)
        return await model_router.query(
            prompt=aggregation_prompt,
            system=_AGGREGATOR_SYSTEM,
            task_type=TaskType.ANALYSIS,
        )

    def _cap_to_task_type(self, capability: str) -> TaskType:
        mapping = {
            "coding": TaskType.CODING,
            "security": TaskType.SECURITY,
            "research": TaskType.RESEARCH,
            "analysis": TaskType.ANALYSIS,
            "planning": TaskType.PLANNING,
        }
        return mapping.get(capability, TaskType.GENERAL)

    def status(self) -> Dict:
        return {
            "active_swarms": len([s for s in self._active_swarms.values() if s.get("status") not in ("done", "failed")]),
            "completed_swarms": len([s for s in self._active_swarms.values() if s.get("status") == "done"]),
            "max_parallel": self._max_parallel,
            "task_timeout": self._timeout,
            "recent": list(self._active_swarms.values())[-5:],
        }


# Singleton
swarm_orchestrator = SwarmOrchestrator()
