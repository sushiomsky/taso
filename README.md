# TASO – Telegram Autonomous Security Operator

> A production-grade, local-first autonomous AI security research platform
> controlled entirely through Telegram.

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-required-blue.svg)](https://docker.com)
[![Commits](https://img.shields.io/badge/commits-53+-brightgreen.svg)](https://github.com/sushiomsky/taso)

---

## Overview

TASO is a modular, open-source autonomous AI operator that runs locally and
exposes its full capability set through a private Telegram bot. It combines
an 11-agent swarm, multi-model LLM routing, defensive security tooling, a
4-component deep web crawler, persistent memory, per-user personalisation, and
a sandboxed self-improvement engine into a single coherent platform.

The entire system runs on your own hardware — no data leaves your machine
except to Telegram for message delivery.

```
Telegram Interface  (NLP routing · confidence thresholds · typing indicators)
        │
Command Gateway  (admin auth · rate limiting · intent classification)
        │
Swarm Orchestrator  (async task planner · agent registry · message bus)
        │
Agent Swarm (11 agents)
  ├── CoordinatorAgent    – task routing, state tracking
  ├── SecurityAgent       – SAST, secret scan, code audit
  ├── ResearchAgent       – CVE feeds, CISA KEV, threat intel
  ├── DevAgent            – code review, patch proposals
  ├── MemoryAgent         – vector store, knowledge DB
  ├── SystemAgent         – host metrics, log access
  ├── PlannerAgent        – task decomposition
  ├── CoderAgent          – code generation & improvement
  ├── AnalysisAgent       – result synthesis
  ├── MonitoringAgent     – system health tracking
  └── SelfHealingAgent    – watchdog, rollback, Git lifecycle
        │
Multi-Model Router  (task-type routing · fallback · Ollama / API)
        │
Tool Execution Layer  (dynamic discovery · sandboxed · schema-validated)
        │
Crawler Stack
  ├── OnionCrawler        – Tor SOCKS5 BFS, .onion address discovery
  ├── ClearnetCrawler     – 40+ security/hacking/cybercrime sites
  ├── IRCIndexer          – Libera/OFTC/EFnet security channels
  └── NewsgroupIndexer    – alt.security, comp.security.*, alt.hacking
        │
Memory + Knowledge System (5 persistent stores)
  ├── FAISS vector store         – semantic search
  ├── SQLite knowledge DB        – CVEs, findings, audit log
  ├── Conversation store         – per-chat LLM history
  ├── Crawler DB (FTS5)          – pages, onions, IRC, newsgroups
  └── User profile store         – per-user personalisation
        │
Self-Improvement Engine  (5-gate safety pipeline · Git versioning · rollback)
        │
Audit & Logging  (structured loguru · searchable · per-category files)
```

---

## Feature List

### 🤖 Agent Swarm

| Feature | Detail |
|---------|--------|
| **11 specialised agents** | CoordinatorAgent, SecurityAgent, ResearchAgent, DevAgent, MemoryAgent, SystemAgent, PlannerAgent, CoderAgent, AnalysisAgent, MonitoringAgent, SelfHealingAgent |
| **Async message bus** | Publish/subscribe pattern; topics follow `domain.action` convention |
| **Parallel execution** | Agents run independently, collaborate via shared memory and bus messages |
| **Dynamic agent spawning** | PlannerAgent can spawn new agents on demand for complex tasks |
| **Task delegation** | CoordinatorAgent routes, tracks state, and aggregates results |
| **Structured messages** | All inter-agent messages are typed, validated, and logged |

---

### 🧠 Multi-Model Router

| Feature | Detail |
|---------|--------|
| **Task-type routing** | Coding → coding model · Analysis → reasoning model · Chat → lightweight model |
| **Dynamic fallback** | Automatically retries with a different model if primary refuses or times out |
| **Provider agnostic** | Supports Ollama (local), GitHub Models API, OpenAI, Anthropic |
| **Abliterated model support** | Routes uncensored/abliterated Ollama models for unrestricted security research tasks that standard models decline |
| **Model registry** | Per-model capability metadata: latency, cost, task suitability |
| **Ollama client** | Full async client with streaming, model listing, and health checks |

---

### 💬 Telegram Interface & NLP

| Feature | Detail |
|---------|--------|
| **Natural language routing** | All free-text messages are classified by a local LLM into 30 intent categories |
| **Fast-path patterns** | 11 obvious inputs (hi, help, status…) skip the LLM entirely — instant response |
| **Confidence thresholds** | < 60% → inline keyboard clarification · 60–75% → act with note · ≥ 75% → act silently |
| **Typing indicators** | `ChatAction.TYPING` fires before every slow operation for real-time feedback |
| **Inline keyboard callbacks** | Confirmation dialogs for uncertain intents with "Yes / No, just chat" choices |
| **Conversation history** | Per-chat LLM context window using stored message history |
| **Rate limiting** | Per-user request throttling to prevent abuse |
| **Admin authentication** | Commands restricted by Telegram user ID and/or username |
| **Long message splitting** | Responses > 4096 chars are automatically chunked |
| **Markdown rendering** | All responses use Telegram MarkdownV2 formatting |

---

### 🛡️ Security Analysis

| Feature | Detail |
|---------|--------|
| **SAST via Bandit** | Static code analysis for Python vulnerabilities |
| **Secret detection** | Regex patterns for API keys, tokens, passwords, private keys |
| **Dependency audit** | `pip-audit` / `npm audit` for known CVEs in dependencies |
| **LLM code audit** | Deep code review via LLM with security-focused system prompt |
| **Repository scanner** | Analyses LOC, languages, commit history, open TODOs, high-risk files |
| **Patch proposal** | DevAgent generates fix suggestions for found vulnerabilities |
| **Risk scoring** | Findings ranked by severity: Critical / High / Medium / Low |
| **Sandboxed execution** | All analysis of external code runs inside isolated Docker containers |

---

### 🌐 Threat Intelligence

| Feature | Detail |
|---------|--------|
| **NVD REST API v2** | Queries NIST National Vulnerability Database with keyword/CPE filters |
| **CISA KEV catalogue** | Fetches and stores the Known Exploited Vulnerabilities catalogue |
| **Tor SOCKS5 crawling** | Optional `.onion` site crawling for dark web threat intelligence |
| **LLM trend analysis** | Synthesises raw CVE data into an executive threat briefing |
| **Persistent CVE storage** | All fetched CVEs stored in structured SQLite knowledge DB |

---

### 🕷️ Crawler Stack

| Feature | Detail |
|---------|--------|
| **Onion crawler** | Async BFS via Tor SOCKS5 (port 9050); rate-limited per domain; depth-limited to 4 hops |
| **Auto-discovers .onion links** | Every `.onion` address found on any page is extracted, registered, and queued |
| **Onion address registry** | Dedicated DB table: address, title, status (alive/dead/timeout), times_seen |
| **Clearnet crawler** | Crawls 40+ seeded security, hacking, and cybercrime research sites |
| **Clearnet seed coverage** | ExploitDB, Krebs, Bleeping Computer, OTX, abuse.ch, NVD, CISA, SecLists, 0day.today, Packet Storm, Full Disclosure, Hacker News, The Register, Wired Security and more |
| **Onion seed coverage** | Dread forum, Ahmia, dark.fail, TorLinks, SecureDrop, OnionLand, Torch, Daniel's .onion list |
| **Domain allow-list** | Clearnet crawler stays within configured domains; expandable at runtime |
| **IRC indexer** | Raw asyncio IRC client; lurks on Libera.Chat, OFTC, and EFnet security channels |
| **IRC channels indexed** | #security, #netsec, #hacking, #malware, #exploits, #tor, #darknet, #osint, #threatintel, #bugbounty, #redteam, #blueteam and more |
| **Newsgroup indexer** | NNTP client for `alt.security`, `alt.hacking`, `comp.security.*`, `sci.crypt`, `alt.privacy.anon-server` etc |
| **FTS5 full-text search** | SQLite FTS5 virtual tables over crawled pages, IRC messages, and newsgroup posts |
| **Text-only storage** | HTML stripped to plain text; images, scripts, stylesheets discarded |
| **Cross-source .onion discovery** | Onion addresses found in clearnet pages, IRC chat, and newsgroups are all registered |
| **Manual URL injection** | Any URL (onion or clearnet) can be added to the queue via `/crawl_add` |
| **Politeness delays** | Per-domain rate limiting (2–3 sec) to avoid overloading targets |
| **Resume capability** | URL queue persists in SQLite; crawler resumes from last position after restart |

---

### 🔧 Tool Execution Framework

| Feature | Detail |
|---------|--------|
| **Dynamic discovery** | All tools in `tools/` auto-loaded at startup via `ToolRegistry` |
| **Standard interface** | Every tool declares: `name`, `description`, `schema`, `execute()` |
| **Dynamic tool generation** | DeveloperAgent generates new tools via LLM, reviewed by SecurityAgent before registration |
| **Sandboxed tool execution** | Tools run in isolated Docker containers with resource limits |
| **`repo_analyzer`** | LOC, language breakdown, commit stats, open TODOs, high-risk patterns |
| **`dependency_scanner`** | pip-audit + npm audit for CVE-flagged dependencies |
| **`web_crawler`** | HTTP + Tor SOCKS5 fetching with content extraction |
| **`system_monitor`** | CPU, RAM, disk, swap, top processes via psutil |
| **`sandbox_runner`** | Isolated subprocess/Docker code execution with stdout/stderr capture |
| **`git_manager`** | Clone, diff, apply patches, commit, push, branch management |
| **`log_analyzer`** | Structured log search with level, time, and keyword filters |
| **Input schema validation** | All tool inputs validated against declared schema before execution |

---

### 📦 Sandbox Execution

| Feature | Detail |
|---------|--------|
| **Docker isolation** | All generated or external code runs in a fresh container |
| **Resource limits** | Configurable memory cap (default 256 MB), CPU quota, execution timeout |
| **No network access** | Sandbox containers have networking disabled by default |
| **Automatic cleanup** | Containers are removed immediately after execution |
| **stdout/stderr capture** | All output captured and returned to the calling agent |
| **Test runner** | Dedicated runner executes the TASO test suite inside Docker for self-improvement gating |

---

### 🔁 Self-Improvement Engine

| Feature | Detail |
|---------|--------|
| **Bug/inefficiency detection** | CodeAnalyzer performs AST analysis, complexity scoring, and pattern matching |
| **LLM patch generation** | PatchGenerator proposes code improvements via LLM |
| **5-gate safety pipeline** | Protected module check → patch size limit → `git apply --check` → tests → static analysis |
| **Protected modules** | `config/`, `sandbox/`, `self_improvement/` are never auto-modified |
| **Patch size limit** | Configurable maximum lines per auto-generated patch |
| **Full audit trail** | Every improvement attempt logged with result, diff, and test outcome |
| **Continuous loop** | Optional background loop that periodically scans and proposes improvements |

---

### 🔒 Git-Based Development Lifecycle

| Feature | Detail |
|---------|--------|
| **Pre-commit hook** | Runs bot command tests + self-healing tests before every commit |
| **Pre-push hook** | Syntax check → 90 unit tests → import smoke test → `_tasks` conflict check |
| **Feature branching** | All autonomous development happens on `bot/dev/<feature>` branches |
| **Commit standards** | Enforced `type(scope): description` format |
| **Self-healing rollback** | On crash, `watchdog.sh` classifies exit type and attempts targeted fix |
| **Smart watchdog** | Classifies: OOM (exit 137) · Telegram conflict (409) · ImportError · NetworkError · Clean exit |
| **Max retry guard** | After 3 identical crashes, watchdog stops and sends Telegram alert — no blind loops |
| **Exponential backoff** | Increasing delay between restart attempts |
| **Repository bootstrap** | On fresh environment, bot auto-restores itself from GitHub |
| **Version tagging** | Stable commits tagged `bot-vMAJOR.MINOR.PATCH` |

---

### 🧬 Personalisation System

| Feature | Detail |
|---------|--------|
| **Per-user profile** | SQLite store: response style, active plugins, learned shortcuts, interaction stats |
| **Behaviour tracking** | Records every intent interaction; infers preferred response style |
| **4 response styles** | `concise` · `technical` · `detailed` · `balanced` — auto-detected from message patterns |
| **Plugin system** | 5 built-in plugins: `security_analyst`, `developer`, `researcher`, `sysadmin`, `power_user` |
| **Auto-activation** | Plugins unlock automatically when usage thresholds are met |
| **Manual activation** | `/activate <plugin_id>` and `/deactivate <plugin_id>` |
| **Shortcut learning** | Repeated phrase→intent mappings become instant fast-path routes (bypasses LLM) |
| **Personalised prompts** | Active plugins inject extra system-prompt hints to shape LLM tone and focus |
| **Unlock notifications** | User notified in-chat when a new plugin auto-activates |
| **Profile command** | `/profile` shows full usage stats, style, active plugins, learned shortcuts |

---

### 🧠 Memory & Knowledge

| Feature | Detail |
|---------|--------|
| **FAISS vector store** | Semantic similarity search over all stored findings and knowledge |
| **SQLite knowledge DB** | Structured storage for CVEs, analyses, tool outputs, audit log |
| **Conversation store** | Per-chat LLM message history for context continuity |
| **User profile store** | Per-user behavioural data, plugins, shortcuts |
| **Crawler DB (FTS5)** | Separate FTS5-indexed DB for crawled pages, onion registry, IRC, newsgroups |
| **Cross-agent memory** | All agents read and write to shared memory for task efficiency |
| **Semantic search** | `/memory <query>` searches CVEs and findings by embedding similarity |
| **Persistent storage** | All DBs survive restarts; crawler queue resumes from last position |

---

### 📋 Logging & Audit

| Feature | Detail |
|---------|--------|
| **Structured logging** | loguru with per-category rotating log files |
| **Log categories** | `bot.log` · `agent.log` · `tool.log` · `security.log` · `self_improvement.log` · `error.log` · `combined.log` · `watchdog.log` |
| **Searchable logs** | `/logs [category]` command with keyword filter |
| **Log monitor** | Background task watches logs for anomalies and triggers self-healing |
| **Audit trail** | Every agent task, tool invocation, self-improvement attempt, and rollback recorded |

---

### ⚙️ System & Operations

| Feature | Detail |
|---------|--------|
| **4 GB swap** | Persistent swap file at `/swapfile`; `vm.swappiness=10` |
| **Memory-safe startup** | Single instance enforcement; duplicate processes terminated before starting |
| **Docker Compose stack** | `taso`, `ollama`, optional `tor` and `postgres` services |
| **Graceful shutdown** | SIGTERM handler cleanly stops all agents, buses, and DB connections |
| **Environment config** | All settings via `.env`; no hardcoded secrets |

---

## Telegram Commands Reference

### Core

| Command | Description | Admin |
|---------|-------------|-------|
| `/start` | Welcome message and role summary | — |
| `/help` | Full command list | — |
| `/status` | System metrics + agent health | ✅ |
| `/system` | Host CPU, RAM, disk, swap, uptime | ✅ |
| `/agents` | Recent agent task history | ✅ |
| `/tools` | List all registered tools | — |
| `/logs [category]` | View recent log lines | ✅ |
| `/memory <query>` | Semantic + CVE knowledge search | ✅ |

### Security & Analysis

| Command | Description | Admin |
|---------|-------------|-------|
| `/scan_repo [path]` | Static analysis of a repository | ✅ |
| `/security_scan [path]` | Full security audit (SAST + deps + secrets) | ✅ |
| `/code_audit` | Audit a pasted code snippet via LLM | ✅ |
| `/threat_intel [keywords]` | Collect CVEs from NVD + CISA KEV | ✅ |
| `/update_self` | Trigger self-improvement pipeline | ✅ |

### Swarm & Models

| Command | Description | Admin |
|---------|-------------|-------|
| `/swarm_status` | Status of all swarm agents | ✅ |
| `/swarm_agents` | List registered agents | ✅ |
| `/swarm_models` | List available LLM models | ✅ |
| `/run_swarm_task <desc>` | Submit a task to the agent swarm | ✅ |
| `/create_agent <spec>` | Dynamically spawn a new agent | ✅ |
| `/create_tool <desc>` | Generate a new tool via LLM | ✅ |
| `/models` | Show model router state | ✅ |

### Crawler

| Command | Description | Admin |
|---------|-------------|-------|
| `/crawl_start [target]` | Start crawlers: all \| onion \| clearnet \| irc \| news | ✅ |
| `/crawl_stop [target]` | Stop crawlers | ✅ |
| `/crawl_status` | DB counts + crawler run state | — |
| `/crawl_add <url>` | Add any URL or `.onion` to the queue | ✅ |
| `/crawl_search <query>` | FTS5 search across all indexed content | — |
| `/crawl_onions [status]` | Browse onion address registry | — |

### Personalisation

| Command | Description | Admin |
|---------|-------------|-------|
| `/profile` | Your usage stats, style, active plugins, shortcuts | — |
| `/plugins` | Browse all available plugins | — |
| `/activate <plugin_id>` | Manually activate a plugin | — |
| `/deactivate <plugin_id>` | Deactivate a plugin | — |

### Git Dev Lifecycle

| Command | Description | Admin |
|---------|-------------|-------|
| `/dev_sync` | Pull latest from GitHub, update context | ✅ |
| `/dev_health` | Run full self-healing health checks | ✅ |
| `/dev_lifecycle` | Run full dev pipeline (sync → test → commit) | ✅ |
| `/dev_branches` | List active `bot/dev/*` feature branches | ✅ |
| `/dev_memory` | Query development memory / suggestions | ✅ |
| `/dev_suggestion` | Submit an improvement suggestion | ✅ |

### Knowledge Management

| Command | Description | Admin |
|---------|-------------|-------|
| `/learn_repo <path>` | Ingest a repository into vector memory | ✅ |
| `/add_feature <desc>` | Log a feature request to dev memory | ✅ |

---

## Quick Start

### Prerequisites

| Tool | Version | Required |
|------|---------|----------|
| Python | 3.11+ | ✅ |
| Docker | 24+ | ✅ (sandbox) |
| Git | 2.x | ✅ |
| Ollama | latest | Optional (local LLM) |
| Tor | 0.4.x | Optional (onion crawler) |

### 1. Clone & configure

```bash
git clone https://github.com/sushiomsky/taso.git
cd taso
cp .env.example .env
nano .env   # Set TELEGRAM_BOT_TOKEN + TELEGRAM_ADMIN_IDS
```

### 2. Install dependencies

```bash
bash install.sh
# or manually:
pip install -r requirements.txt
```

### 3. Optional: start Ollama with an abliterated model

```bash
ollama pull hf.co/mradermacher/Llama-3.1-8B-Abliterated-GGUF
ollama serve
```

### 4. Run

```bash
# Development
python3 main.py

# Production (with watchdog supervisor)
nohup bash watchdog.sh > logs/watchdog.log 2>&1 &

# Docker Compose
docker compose up -d
```

### 5. Start crawling (optional)

In Telegram, send:
```
/crawl_start all
```
Requires Tor on `127.0.0.1:9050` for onion crawling.
Clearnet, IRC, and newsgroup crawling work without Tor.

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

| Topic prefix | Owner |
|---|---|
| `coordinator.*` | CoordinatorAgent |
| `security.*` | SecurityAgent |
| `research.*` | ResearchAgent |
| `dev.*` | DevAgent |
| `memory.*` | MemoryAgent |
| `system.*` | SystemAgent |
| `crawler.*` | CrawlerManager |

### Self-Improvement Safety Gates

```
Patch proposal
      │
  Gate 1 ─ Protected module check    (config/, sandbox/, self_improvement/)
      │
  Gate 2 ─ Patch size limit          (< MAX_PATCH_LINES)
      │
  Gate 3 ─ git apply --check         (valid diff)
      │
  Gate 4 ─ Test suite in sandbox     (all 90 tests pass)
      │
  Gate 5 ─ Static analysis score     (no regression)
      │
  ✅ Commit + Audit Log + Version Tag
```

### Watchdog Crash Classification

```
Exit code 137      → OOM           → clear cache, restart
Log contains "409" → TG_CONFLICT   → stop duplicate process, restart
Log: "ImportError" → IMPORT_ERROR  → pip install missing, restart
Log: "Network"     → NETWORK_ERROR → wait + restart
Exit code 0        → CLEAN_EXIT    → do NOT restart
3× same crash      → GIVE_UP       → send Telegram alert, stop
```

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Required — from @BotFather |
| `TELEGRAM_ADMIN_IDS` | — | Comma-separated numeric Telegram IDs |
| `TELEGRAM_ADMIN_USERNAMES` | — | Comma-separated @usernames (alternative) |
| `LLM_BACKEND` | `ollama` | `ollama` / `copilot` / `openai` / `anthropic` |
| `OLLAMA_MODEL` | `llama3` | Default Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `SELF_IMPROVE_ENABLED` | `false` | Enable autonomous patching loop |
| `MAX_PATCH_LINES` | `500` | Maximum lines per auto-generated patch |
| `PROTECTED_MODULES` | `config,sandbox,self_improvement` | Never auto-patched |
| `TOR_ENABLED` | `false` | Enable Tor SOCKS5 for onion crawling |
| `TOR_PROXY` | `socks5://127.0.0.1:9050` | Tor proxy address |
| `DOCKER_MEM_LIMIT` | `256m` | Sandbox container memory cap |
| `DOCKER_TIMEOUT` | `60` | Sandbox execution timeout (seconds) |
| `SWARM_ENABLED` | `true` | Enable multi-agent swarm |
| `RATE_LIMIT_MESSAGES` | `10` | Max messages per user per minute |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Project Structure

```
taso/
├── main.py                        ← entry point
├── orchestrator.py                ← full lifecycle manager
├── watchdog.sh                    ← smart crash-classifying supervisor
├── requirements.txt
├── Dockerfile / docker-compose.yml
├── install.sh / .env.example
│
├── bot/
│   └── telegram_bot.py            ← NLP routing, all command handlers (40+ commands)
│
├── agents/                        ← 11 specialised agents
│   ├── message_bus.py
│   ├── base_agent.py
│   ├── coordinator_agent.py
│   ├── security_agent.py
│   ├── research_agent.py
│   ├── dev_agent.py
│   ├── memory_agent.py
│   ├── system_agent.py
│   ├── planner_agent.py
│   ├── coder_agent.py
│   ├── analysis_agent.py
│   ├── monitoring_agent.py
│   └── self_healing_agent.py
│
├── tools/                         ← tool framework + built-in tools
│   ├── base_tool.py
│   ├── repo_analyzer.py
│   ├── dependency_scanner.py
│   ├── web_crawler.py
│   ├── system_monitor.py
│   ├── sandbox_runner.py
│   ├── git_manager.py
│   ├── log_analyzer.py
│   ├── tool_registry.py
│   ├── system_tools.py
│   └── dynamic_tool_generator.py
│
├── crawler/                       ← deep web + clearnet + IRC + NNTP
│   ├── crawler_db.py
│   ├── onion_crawler.py
│   ├── clearnet_crawler.py
│   ├── irc_indexer.py
│   ├── newsgroup_indexer.py
│   ├── crawler_manager.py
│   ├── text_extractor.py
│   └── seed_urls.py
│
├── models/                        ← multi-model router
│   ├── model_router.py
│   ├── model_registry.py
│   └── ollama_client.py
│
├── memory/                        ← 5 persistence stores
│   ├── vector_store.py
│   ├── knowledge_db.py
│   ├── conversation_store.py
│   └── user_profile_store.py
│
├── personalization/               ← per-user adaptation
│   ├── personalization_engine.py
│   ├── behavior_tracker.py
│   └── plugin_manager.py
│
├── swarm/
│   ├── swarm_orchestrator.py
│   ├── task_planner.py
│   └── agent_registry.py
│
├── sandbox/
│   ├── docker_runner.py
│   └── test_runner.py
│
├── self_improvement/
│   ├── code_analyzer.py
│   ├── patch_generator.py
│   └── auto_deployer.py
│
├── self_healing/
│   ├── git_manager.py
│   ├── deploy_manager.py
│   └── version_history_db.py
│
├── config/
│   ├── settings.py
│   └── logging_config.py
│
├── tests/
│   ├── test_bot_commands.py
│   ├── test_git_self_healing.py
│   └── test_personalization.py
│
└── logs/
    ├── combined.log / bot.log / agent.log
    ├── tool.log / security.log / error.log
    ├── self_improvement.log
    └── watchdog.log
```

---

## Example Workflows

### 1 — Repository Security Audit

```
User → /scan_repo /home/user/myproject
  → SecurityAgent
      ├── Bandit SAST
      ├── Secret detection
      └── LLM executive summary
  → MemoryAgent → store findings
  → "3 HIGH, 7 MEDIUM · Top: hardcoded credential in config.py:42"
```

### 2 — Dark Web Threat Intelligence

```
User → /crawl_start onion
  → OnionCrawler via Tor SOCKS5
  → Dread, dark.fail, Ahmia, TorLinks
  → extract text + discover new .onion addresses
  → User → /crawl_search "CVE-2024-xxxx"
  → FTS5 results from dark web pages
```

### 3 — Adaptive Personalisation

```
User sends 6× "scan my code" → security_scan intent
  → BehaviorTracker learns phrase → intent mapping
  → After 4 consistent uses: shortcut registered
  → Future: "scan my code" → instant route, no LLM
  → security_analyst plugin auto-activates
  → LLM gains system hint: "Be highly technical, use CVE IDs…"
```

### 4 — Self-Improvement Pipeline

```
CodeAnalyzer → high-complexity function detected
  → PatchGenerator proposes refactor
  → Gates 1–5 all pass (tests: 90/90)
  → git commit + tag bot-v1.4.1
  → Audit log entry written
```

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

- All sensitive commands enforce **admin-only access** by numeric user ID and/or username.
- All code execution runs **sandboxed in Docker** — no network, memory-limited, CPU-capped, auto-cleaned.
- The self-improvement engine has **5 mandatory safety gates**; protected modules are never touched.
- Secrets are loaded from `.env` — never hardcoded.
- Tor is **disabled by default**; opt-in with `TOR_ENABLED=true` or `/crawl_start onion`.
- The watchdog enforces a **maximum retry count** before alerting and stopping — no silent crash loops.
- Pre-push hooks **block all pushes** unless 90 unit tests pass.

---

## License

MIT License — see [LICENSE](LICENSE)

---

## Disclaimer

TASO is designed exclusively for **defensive security research** on systems
you own or have explicit authorisation to test. The crawler accesses only
publicly available information. The authors accept no responsibility for
misuse. All tool execution occurs locally on your own infrastructure.
