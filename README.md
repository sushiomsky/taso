# TASO – Telegram Autonomous Security Operator

> A production-grade, local-first autonomous AI security research platform
> controlled entirely through Telegram.

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-required-blue.svg)](https://docker.com)

---

## Overview

TASO is a modular, open-source autonomous AI operator that runs locally and
exposes its capabilities through a private Telegram bot.  It combines a
multi-agent system, defensive security tooling, threat intelligence collection,
persistent memory, and a sandboxed self-improvement engine into a single,
coherent platform.

```
Telegram Interface
      │
Command Gateway (TelegramBot)
      │
Agent Orchestrator (CoordinatorAgent)
      │
Multi-Agent System
  ├── SecurityAnalysisAgent  – static analysis, vulnerability detection
  ├── ResearchAgent          – CVE feeds, CISA KEV, Tor intel
  ├── DevAgent               – code review, patch proposals
  ├── MemoryAgent            – vector store, knowledge DB
  └── SystemAgent            – host metrics, log access
      │
Tool Execution Layer
  ├── repo_analyzer          – LOC, languages, commits, TODOs
  ├── dependency_scanner     – pip-audit / npm audit
  ├── web_crawler            – HTTP + Tor SOCKS5
  ├── system_monitor         – psutil metrics
  ├── sandbox_runner         – isolated Docker execution
  ├── git_manager            – clone, diff, apply patches, commit
  └── log_analyzer           – structured log search
      │
Sandbox (Docker)
      │
Memory + Knowledge System
  ├── FAISS vector store     – semantic search
  ├── SQLite knowledge DB    – CVEs, findings, audit log
  └── Conversation store     – per-chat history
      │
Self-Improvement Engine
  ├── CodeAnalyzer           – static analysis, complexity, secrets
  ├── PatchGenerator         – LLM-assisted fix proposals
  └── AutoDeployer           – multi-gate safety pipeline
```

---

## Features

| Category               | Capability |
|------------------------|------------|
| 🤖 **AI Agents**       | 6 specialist agents, async message bus, coordinator orchestration |
| 🛡️ **Security**        | Bandit SAST, secret scanning, dependency CVE audit, code audit via LLM |
| 🌐 **Threat Intel**    | NVD REST API v2, CISA KEV catalogue, optional Tor SOCKS5 crawling |
| 🧠 **Memory**          | FAISS semantic search, SQLite structured storage, conversation history |
| 🔧 **Self-Improvement**| 5-gate safety pipeline – tests, static analysis, patch size limits |
| 📦 **Sandbox**         | Docker isolation, memory/CPU limits, no network, auto-cleanup |
| 📋 **Audit Log**       | All actions logged with actor, target, status, timestamp |
| 🔒 **Access Control**  | Admin-only Telegram commands, rate limiting |

---

## Quick Start

### Prerequisites

| Tool | Version | Required |
|------|---------|----------|
| Python | 3.11+ | ✅ |
| Docker | 24+ | ✅ (sandbox) |
| Git | 2.x | ✅ |
| Ollama | latest | Optional (local LLM) |
| Tor | 0.4.x | Optional (threat intel) |

### 1. Clone the repository

```bash
git clone https://github.com/yourorg/taso.git
cd taso/telegram_autonomous_security_operator
```

### 2. Install dependencies

```bash
bash install.sh
```

Or manually:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env – minimum required fields:
#   TELEGRAM_BOT_TOKEN  – from @BotFather
#   TELEGRAM_ADMIN_IDS  – your Telegram user ID(s)
#   LLM_BACKEND         – ollama | openai | anthropic
nano .env
```

### 4. (Optional) Start Ollama

```bash
ollama pull llama3
ollama serve
```

### 5. Run TASO

```bash
source .venv/bin/activate
python main.py
```

### 6. Docker Compose (recommended for production)

```bash
cp .env.example .env && nano .env
docker compose up -d
# Pull the default LLM model into Ollama
docker exec taso_ollama ollama pull llama3
```

---

## Telegram Commands

All commands require admin authentication unless noted.

| Command | Description | Admin Only |
|---------|-------------|-----------|
| `/start` | Welcome and show role | No |
| `/help` | List all commands | No |
| `/tools` | List available tools | No |
| `/status` | System metrics snapshot | ✅ |
| `/agents` | Recent agent task history | ✅ |
| `/memory <query>` | Semantic + CVE knowledge search | ✅ |
| `/scan_repo [path]` | Static analysis of a repository | ✅ |
| `/security_scan [path]` | Full security audit (SAST + deps + secrets) | ✅ |
| `/code_audit` | Audit a code snippet (paste after command) | ✅ |
| `/threat_intel [keywords]` | Collect CVEs from NVD + CISA | ✅ |
| `/update_self` | Propose self-improvement patches | ✅ |
| `/logs [category]` | View recent log lines | ✅ |
| `/system` | Host resource metrics | ✅ |

**Free-text messages** are routed to the LLM for conversational responses
with per-chat history.

---

## Architecture Details

### Message Bus

All agents communicate via an async publish/subscribe message bus.

```
Publisher                    Bus                       Subscriber
  │                           │                           │
  │──── BusMessage ──────────>│──── topic match ─────────>│
  │     topic: "security.scan_repo"                       │
  │     payload: {...}                                     │
  │     reply_to: "bot.reply.123"                         │
  │<──────────────────────────│<──── result ──────────────│
```

Topics follow a `domain.action` convention:

| Prefix | Owner |
|--------|-------|
| `coordinator.*` | CoordinatorAgent |
| `security.*` | SecurityAnalysisAgent |
| `research.*` | ResearchAgent |
| `dev.*` | DevAgent |
| `memory.*` | MemoryAgent |
| `system.*` | SystemAgent |

### Memory Architecture

```
User query / agent findings
         │
         ▼
  VectorStore (FAISS)        ◄── semantic similarity search
         │
  KnowledgeDB (SQLite)       ◄── structured: CVEs, analyses, audit log
         │
  ConversationStore (SQLite) ◄── per-chat LLM history
```

### Self-Improvement Safety Gates

```
Patch proposal
      │
  Gate 1 ─ Protected module check      (config/, sandbox/, self_improvement/)
      │
  Gate 2 ─ Patch size limit            (< MAX_PATCH_LINES lines)
      │
  Gate 3 ─ git apply --check           (syntactically valid diff)
      │
  Gate 4 ─ Test suite in sandbox       (all tests must pass)
      │
  Gate 5 ─ Static analysis score       (must not regress)
      │
  ✅ Commit + Audit Log
```

---

## Configuration Reference

See `.env.example` for all options.  Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | – | Required: bot token from @BotFather |
| `TELEGRAM_ADMIN_IDS` | – | Comma-separated admin user IDs |
| `LLM_BACKEND` | `ollama` | `ollama` / `openai` / `anthropic` |
| `OLLAMA_MODEL` | `llama3` | Model name for Ollama |
| `SELF_IMPROVE_ENABLED` | `false` | Enable autonomous patching |
| `MAX_PATCH_LINES` | `500` | Maximum lines per auto-patch |
| `PROTECTED_MODULES` | `config,sandbox,self_improvement` | Never auto-patched |
| `TOR_ENABLED` | `false` | Enable Tor SOCKS5 for crawling |
| `DOCKER_MEM_LIMIT` | `256m` | Sandbox container memory |
| `DOCKER_TIMEOUT` | `60` | Sandbox execution timeout (seconds) |

---

## Adding Custom Tools

1. Create `tools/my_tool.py`
2. Subclass `BaseTool` and set `name`, `description`, `schema`
3. Implement `async execute(**kwargs) -> Any`
4. The `ToolRegistry` auto-discovers it on startup

```python
# tools/my_tool.py
from tools.base_tool import BaseTool, ToolSchema

class MyCustomTool(BaseTool):
    name        = "my_tool"
    description = "Does something useful."
    schema      = ToolSchema({
        "target": {"type": "str", "required": True, "description": "..."},
    })

    async def execute(self, target: str, **_):
        # your logic here
        return {"result": f"Processed: {target}"}
```

---

## Adding Custom Agents

1. Create `agents/my_agent.py`
2. Subclass `BaseAgent`
3. Implement `_register_subscriptions()` with bus topic handlers
4. Instantiate and start in `orchestrator.py`

---

## Security Considerations

- The Telegram bot enforces **admin-only access** for all sensitive operations.
- All code execution is **sandboxed in Docker** with: no network, memory limits,
  CPU quotas, read-only filesystem, dropped capabilities.
- The self-improvement engine has **multiple safety gates** and a comprehensive
  audit trail. Protected modules are never auto-modified.
- Secrets are loaded from `.env` – never hardcoded.
- Tor is **disabled by default**; enable explicitly with `TOR_ENABLED=true`.

---

## Project Structure

```
telegram_autonomous_security_operator/
├── main.py                      ← entry point
├── orchestrator.py              ← lifecycle manager
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── install.sh
├── .env.example
│
├── bot/
│   └── telegram_bot.py          ← Telegram interface
│
├── agents/
│   ├── message_bus.py           ← async pub/sub bus
│   ├── base_agent.py            ← abstract base + LLM helpers
│   ├── coordinator_agent.py     ← task routing, state tracking
│   ├── security_agent.py        ← SAST, secret scan, code audit
│   ├── research_agent.py        ← NVD, CISA, Tor intel
│   ├── dev_agent.py             ← code review, patch proposals
│   ├── memory_agent.py          ← knowledge storage + retrieval
│   └── system_agent.py          ← host metrics, log access
│
├── tools/
│   ├── base_tool.py             ← base class + tool registry
│   ├── repo_analyzer.py
│   ├── dependency_scanner.py
│   ├── web_crawler.py
│   ├── system_monitor.py
│   ├── sandbox_runner.py
│   ├── git_manager.py
│   └── log_analyzer.py
│
├── memory/
│   ├── vector_store.py          ← FAISS semantic memory
│   ├── knowledge_db.py          ← SQLite structured storage
│   └── conversation_store.py   ← per-chat history
│
├── sandbox/
│   ├── docker_runner.py         ← low-level Docker execution
│   └── test_runner.py           ← test suite runner for self-improve
│
├── self_improvement/
│   ├── code_analyzer.py         ← AST + pattern-based analysis
│   ├── patch_generator.py       ← LLM + rule-based patch generation
│   └── auto_deployer.py         ← multi-gate deployment pipeline
│
├── config/
│   ├── settings.py              ← environment-based configuration
│   └── logging_config.py        ← loguru structured logging
│
└── logs/                        ← rotating log files (auto-created)
    ├── combined.log
    ├── agent.log
    ├── tool.log
    ├── security.log
    ├── self_improvement.log
    └── error.log
```

---

## Example Agent Workflows

### Workflow 1: Repository Security Audit

```
User → /scan_repo /home/user/myproject
  → TelegramBot → coordinator.task {command: scan_repo}
  → CoordinatorAgent → security.scan_repo
  → SecurityAnalysisAgent
      ├── bandit SAST analysis
      ├── regex secret detection
      └── LLM executive summary
  → memory.store (findings persisted)
  → TelegramBot → User: summary + finding counts
```

### Workflow 2: Threat Intelligence Collection

```
User → /threat_intel log4j
  → coordinator.task {command: threat_intel, keywords: ["log4j"]}
  → ResearchAgent
      ├── NVD API query (keyword: log4j)
      ├── CISA KEV catalogue
      └── LLM trend analysis
  → memory.store_cve (each CVE)
  → TelegramBot → User: CVE count + analysis
```

### Workflow 3: Conversational Security Q&A

```
User → "What's the CVSS score of CVE-2021-44228?"
  → ConversationStore (add to history)
  → LLM query with system prompt + history
  → (optionally) memory.query (vector search for CVE data)
  → TelegramBot → User: LLM response
```

---

## License

MIT License – see [LICENSE](LICENSE)

---

## Disclaimer

TASO is designed exclusively for **defensive security research** on systems
you own or have explicit authorisation to test.  The authors accept no
responsibility for misuse.  All tool execution occurs locally on your
infrastructure.
