#!/usr/bin/env python3
"""
TASO Example: Agent Swarm Workflow
===================================
Demonstrates how the swarm orchestrator decomposes a user request
into subtasks and runs them in parallel through specialized agents.

Run with:
    cd /root/taso && python examples/swarm_workflow.py
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.logging_config import configure_logging

configure_logging()


async def demo_swarm_task():
    """Run a security analysis task through the full swarm pipeline."""
    from agents.message_bus import bus
    from memory.knowledge_db import KnowledgeDB
    from memory.vector_store import VectorStore
    from memory.conversation_store import ConversationStore
    from agents.planner_agent import PlannerAgent
    from agents.research_agent import ResearchAgent
    from agents.security_agent import SecurityAnalysisAgent
    from agents.analysis_agent import AnalysisAgent
    from agents.coder_agent import CoderAgent
    from agents.memory_agent import MemoryAgent
    from swarm.swarm_orchestrator import SwarmOrchestrator
    from swarm.agent_registry import AgentRegistry

    # 1. Wire up a minimal in-memory store
    db = KnowledgeDB()
    await db.connect()
    vector = VectorStore()
    conv   = ConversationStore()

    # 2. Start agents
    agents = []
    for cls in [PlannerAgent, SecurityAnalysisAgent, ResearchAgent, AnalysisAgent, CoderAgent]:
        a = cls(bus)
        await a.start()
        agents.append(a)

    mem = MemoryAgent(bus, db, vector, conv)
    await mem.start()
    agents.append(mem)

    # 3. Register agents in swarm registry
    registry = AgentRegistry()
    from swarm.agent_registry import register_default_agents
    register_default_agents({a.name: a for a in agents})

    # 4. Run a swarm task
    orchestrator = SwarmOrchestrator(registry=registry)

    request = (
        "Analyse the TASO project at /root/taso for security vulnerabilities. "
        "Summarise the top 3 findings and propose one concrete code fix."
    )

    print(f"\n{'='*60}")
    print("SWARM TASK:")
    print(request)
    print('='*60)

    result = await orchestrator.run(request)

    print("\nSWARM RESULT:")
    print('-'*60)
    print(result)
    print('-'*60)

    swarm_status = orchestrator.status()
    print(f"\nSwarm stats: {swarm_status}")

    # 5. Cleanup
    for a in agents:
        await a.stop()
    await db.close()


async def demo_parallel_execution():
    """Show that independent subtasks run in parallel (timing proof)."""
    from swarm.task_planner import SubTask, TaskPlan
    from swarm.swarm_orchestrator import SwarmOrchestrator
    from swarm.agent_registry import AgentRegistry
    import time

    registry = AgentRegistry()
    orch = SwarmOrchestrator(registry=registry, max_parallel=4, task_timeout=30)

    # Build a simple plan with 3 independent subtasks
    subtasks = [
        SubTask(id="t1", description="sleep 2 seconds", capability="general", depends_on=[], status="pending"),
        SubTask(id="t2", description="sleep 2 seconds", capability="general", depends_on=[], status="pending"),
        SubTask(id="t3", description="sleep 2 seconds", capability="general", depends_on=[], status="pending"),
    ]
    plan = TaskPlan(
        original_request="parallel timing test",
        subtasks=subtasks,
    )

    # Simulate agent execution (2s each)
    async def fake_handler(desc, ctx=""):
        await asyncio.sleep(2)
        return f"done: {desc}"

    class FakeAgent:
        name = "general"
        async def handler(self, d, c): return await fake_handler(d, c)

    registry.register("general", FakeAgent(), {"general"})

    start = time.monotonic()
    await orch._execute_dag(plan)
    elapsed = time.monotonic() - start

    print(f"\nParallel execution test:")
    print(f"  3 tasks × 2s each = {elapsed:.1f}s total (should be ~2s if parallel)")
    for t in subtasks:
        print(f"  [{t.id}] status={t.status}  result={t.result}")


if __name__ == "__main__":
    print("TASO Swarm Workflow Demo\n")
    asyncio.run(demo_parallel_execution())
    print("\n(Full swarm task requires active LLM backend — see README)")
