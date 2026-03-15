# TASO Architecture

```
Telegram Interface  (@AegisHex_bot)
       │
       ▼
Command Gateway  (bot/telegram_bot.py)
  NLP intent classifier → /command handlers
       │
       ▼
Swarm Orchestrator  (swarm/swarm_orchestrator.py)
  asyncio.gather parallel DAG execution
       │
       ▼
Task Planner  (swarm/task_planner.py)
  LLM decomposes requests → SubTask DAG
       │
       ▼
Agent Swarm  (12 agents, agents/*.py)
  ┌──────────────┬──────────────┬──────────────┐
  │ PlannerAgent │ CoderAgent   │ ResearchAgent│
  │ DevAgent     │ SecurityAgent│ AnalysisAgent│
  │ MemoryAgent  │ SystemAgent  │ SelfHealing  │
  │ Developer    │ Coordinator  │ Monitoring   │
  └──────────────┴──────────────┴──────────────┘
  Agents communicate via MessageBus (pub/sub)
       │
       ▼
Multi-Model Router  (models/model_router.py)
  Primary (GitHub Models / gpt-4o)
  → Fallback (Ollama / llama3)
  → Uncensored (dolphin-mistral)
       │
       ▼
Tool System  (tools/)
  Static: repo_analyzer, dependency_scanner, web_crawler,
          system_monitor, git_manager, log_monitor,
          system_tools (5 tools)
  Dynamic: generated via DeveloperAgent + sandbox tested
  Registry: ToolRegistry auto-discovers all BaseTool subclasses
       │
       ▼
Shared Memory
  ┌────────────────────┬─────────────────────┐
  │ VectorStore (FAISS)│ KnowledgeDB (SQLite) │
  │ semantic embeddings│ advisories, CVEs     │
  └────────────────────┴─────────────────────┘
  + ConversationStore (per-chat history)
  + AuditLog (append-only action log)
  + VersionHistoryDB (commit metadata)
       │
       ▼
Sandbox Execution  (sandbox/)
  Docker containers (python:3.11-slim)
  Resource limits: memory=256m, cpu-quota=50000
  Network: none by default
  Auto-cleanup on timeout
       │
       ▼
Self-Improvement Engine  (self_healing/ + self_improvement/)
  CodeAnalyzer → PatchGenerator → SandboxTest → AutoDeployer
  Git: commit → tag → push to GitHub
  Rollback: revert on runtime error
       │
       ▼
Audit & Logging  (memory/audit_log.py + logs/)
  All actions → audit_log.db (SQLite)
  All logs → logs/agent.log (loguru, rotating)
```

## Message Bus

All agents communicate through `agents/message_bus.py` — a lightweight
async pub/sub bus. Topics use dot-notation:

```
security.scan_repo       → SecurityAnalysisAgent
developer.create_agent   → DeveloperAgent
monitoring.status        → MonitoringAgent
memory.store             → MemoryAgent
coordinator.heartbeat    → CoordinatorAgent
```

Wildcard subscription: subscribing to `"security"` matches
`security.scan_repo`, `security.full_scan`, etc.

## Data Flow — Swarm Task

```
User: "scan this repo for CVEs and propose fixes"
  │
  ▼ NLP classifier → intent: swarm_task
  ▼ SwarmOrchestrator.run(request)
  ▼ TaskPlanner.plan()  [LLM decomposes into subtasks]
    SubTask(security_scan, capability=security)
    SubTask(research_cves, capability=research, depends_on=[security_scan])
    SubTask(propose_fixes, capability=coding, depends_on=[research_cves])
  ▼ _execute_dag()
    Round 1: asyncio.gather(security_scan)
    Round 2: asyncio.gather(research_cves)   ← waits for security_scan
    Round 3: asyncio.gather(propose_fixes)   ← waits for research_cves
  ▼ _aggregate() → final LLM summary
  ▼ Telegram reply to user
```

## Data Flow — Dynamic Agent Creation

```
User: /create_agent An agent that monitors Tor hidden services
  │
  ▼ _cmd_create_agent → DeveloperAgent.create_agent(description)
  ▼ ModelRouter.query(prompt, task_type=CODING) → agent code
  ▼ TestRunner.syntax_check_code(code)
  ▼ agents/tor_monitoring_agent.py written to disk
  ▼ importlib.import_module() + agent_registry.register()
  ▼ AuditLog.record(action="create_agent")
  ▼ Telegram: "✅ Agent 'TorMonitoringAgent' created and live"
```

## Self-Improvement Loop

```
Trigger: /update_self  OR  self_improve_loop() timer
  │
  ▼ CodeAnalyzer.analyse_repo() → findings (AST, patterns, complexity)
  ▼ PatchGenerator.generate_for_file() → PatchProposal (unified diff)
  ▼ Safety gates:
      _gate_protection() → refuse if file is in PROTECTED_MODULES
      _gate_size()       → refuse if diff > MAX_PATCH_LINES
      _gate_git_check()  → refuse if not on a clean branch
  ▼ AutoDeployer.evaluate_and_deploy()
      sandbox test → run_tests() → pass/fail
      on pass: git commit + tag + push
      on fail:  AuditLog failure, no deploy
  ▼ RollbackManager monitors runtime errors
      on threshold exceeded: git revert → redeploy
```
