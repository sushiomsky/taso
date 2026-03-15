# TASO Agent Reference

TASO runs a swarm of 12 specialized agents that communicate through a
pub/sub message bus.  Each agent:

- Subscribes to one or more bus topics
- Implements `handle(description, context)` for direct swarm calls
- Uses `llm_query()` inherited from `BaseAgent`
- Can publish messages to other agents

---

## Agent Overview

| Agent | Class | Bus Topics | Capabilities |
|---|---|---|---|
| coordinator | CoordinatorAgent | coordinator.* | planning, general |
| security | SecurityAnalysisAgent | security.* | security, analysis |
| research | ResearchAgent | research.* | research, general |
| dev | DevAgent | dev.* | coding, general |
| memory | MemoryAgent | memory.* | general |
| system | SystemAgent | system.* | general |
| planner | PlannerAgent | planner.* | planning, general |
| coder | CoderAgent | coder.* | coding |
| analysis | AnalysisAgent | analysis.* | analysis, research |
| developer | DeveloperAgent | developer.* | general |
| self_healing | SelfHealingAgent | self_healing.* | general |
| monitoring | MonitoringAgent | monitoring.* | general |

---

## PlannerAgent

Decomposes complex user requests into a DAG of subtasks.
Uses the ModelRouter with `TaskType.PLANNING` to route to the
best reasoning model available.

**Topics:** `planner.plan`
**Returns:** `TaskPlan` with list of `SubTask` objects

---

## SecurityAnalysisAgent

Performs defensive security analysis.

**Topics:**
- `security.scan_repo` — run bandit + secret scan on a repo path
- `security.full_scan` — bandit + safety dep scan + secret scan
- `security.code_audit` — LLM-assisted deep code audit

**Tools used:** bandit, safety, regex secret scanner

---

## ResearchAgent

Collects threat intelligence and learns from external sources.

**Topics:**
- `research.threat_intel` — crawl CVE feeds, security advisories
- `research.learn_repo` — fetch and store GitHub repo knowledge
- `research.web` — crawl a URL and store findings

---

## DeveloperAgent

Generates code, patches, and new agents/tools via LLM.

**Topics:**
- `developer.request` — generic dev task (action: generate/patch/tool)
- `developer.create_agent` — autonomous new agent generation

**Key methods:**
- `create_agent(description)` → generates, tests, saves, registers agent
- `_generate_tool(task)` → generates, sandbox-tests, registers tool
- `_generate_patch(task, context)` → produces unified diff

---

## MonitoringAgent

Tracks system health and publishes periodic heartbeats.

**Topics:**
- `monitoring.status` — full health snapshot (CPU/RAM/disk/alerts)
- `monitoring.alert` — register a manual alert
- `monitoring.errors` — recent log error summary
- `monitoring.metrics` — raw metric snapshot

**Background:** publishes `coordinator.heartbeat` every 60 seconds.
Auto-alerts admin when CPU > 90%, RAM > 90%, or Disk > 95%.

---

## MemoryAgent

Manages knowledge storage and retrieval across all agents.

**Topics:**
- `memory.store` — store text + metadata in KnowledgeDB / VectorStore
- `memory.query` — semantic search against stored knowledge
- `memory.conversation` — retrieve conversation history

---

## SelfHealingAgent

Coordinates the self-improvement pipeline.

**Topics:**
- `self_healing.patch` — apply a code patch through full pipeline
- `self_healing.rollback` — revert to last stable commit

**Pipeline:** CodeAnalyzer → PatchGenerator → sandbox test →
AutoDeployer → git commit + push → AuditLog record

---

## Creating a New Agent

### Via Telegram
```
/create_agent A monitoring agent that watches Docker container health
```

### Via Python
```python
from agents.developer_agent import DeveloperAgent
from agents.message_bus import bus

dev = DeveloperAgent(bus)
result = await dev.create_agent(
    description="Monitor Docker containers and alert on restarts",
    agent_name="docker_monitor_agent",
)
```

### Manually

Create `agents/my_agent.py`:

```python
from __future__ import annotations
from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage
from config.logging_config import get_logger

log = get_logger("agent")

class MyAgent(BaseAgent):
    name        = "my_agent"
    description = "Does X, Y, Z."

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("my_agent.do_thing", self._handle_thing)

    async def _handle_thing(self, msg: BusMessage) -> None:
        result = await self.llm_query(msg.payload.get("prompt", ""))
        if msg.reply_to:
            await self.publish(msg.reply_to, {"result": result})

    async def handle(self, description: str, context: str = "") -> str:
        return await self.llm_query(description)
```

Register in `orchestrator.py` by adding to `agent_classes` list.
