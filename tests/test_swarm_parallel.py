"""
Swarm orchestrator parallel-execution regression tests.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_execute_dag_runs_ready_tasks_concurrently():
    """
    Independent ready tasks should start in the same wave.

    This test deadlocks under sequential execution because each subtask waits
    for all others to start. It passes only when _execute_dag schedules them
    concurrently (via asyncio.gather).
    """
    from swarm.task_planner import TaskPlan, SubTask
    from swarm.swarm_orchestrator import SwarmOrchestrator
    from swarm.agent_registry import AgentRegistry

    subtasks = [
        SubTask(id="a", description="task a", capability="general", depends_on=[]),
        SubTask(id="b", description="task b", capability="general", depends_on=[]),
        SubTask(id="c", description="task c", capability="general", depends_on=[]),
    ]
    plan = TaskPlan(original_request="parallel proof", subtasks=subtasks)

    orch = SwarmOrchestrator(registry=AgentRegistry(), max_parallel=5, task_timeout=5)

    started = 0
    started_lock = asyncio.Lock()
    all_started = asyncio.Event()

    async def fake_run_subtask(task, _plan):
        nonlocal started
        async with started_lock:
            started += 1
            if started == len(subtasks):
                all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1.5)
        task.status = "done"
        task.result = "ok"

    # Monkey-patch to isolate scheduling behavior in _execute_dag.
    orch._run_subtask = fake_run_subtask  # type: ignore[assignment]

    await orch._execute_dag(plan)

    assert started == 3
    assert all(t.status == "done" for t in plan.subtasks)
