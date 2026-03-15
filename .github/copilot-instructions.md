# TASO – Copilot Instructions

Telegram Autonomous Security Operator: an async Python bot that coordinates an AI agent swarm for cybersecurity research, code analysis, and self-healing automation.

## Running the project

```bash
# Install dependencies (system Python, no venv)
pip install -r requirements.txt --break-system-packages

# Copy and configure credentials
cp .env.example .env
# Minimum required: TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_USERNAMES, GITHUB_TOKEN

# Start the bot
python main.py

# Run the refactor/improvement daemon (separate process)
nohup python refactor_daemon.py > /dev/null 2>&1 &
```

There is no test suite or linter configured. Syntax validation only:
```bash
python -m py_compile <file.py>
# Or check all at once:
find . -name "*.py" | xargs python -m py_compile
```

## Architecture

### Startup sequence
`main.py` → `Orchestrator.run()` → in order:
1. `MessageBus.start()` — async pub/sub backbone
2. Memory subsystem (`KnowledgeDB`, `VectorStore`, `ConversationStore`)
3. `ToolRegistry.discover()` — auto-imports all `tools/*.py`
4. All 11 agents started concurrently, each subscribing to bus topics
5. Swarm registry populated with agent handles
6. `TelegramBot.start()` — PTB application polling begins
7. Signal handlers registered via `loop.add_signal_handler()` (NOT `signal.signal()`)

### Agent communication
All inter-agent messages use `BusMessage(topic, sender, payload, reply_to)`. Topic routing is prefix-matched: subscribing to `"security"` catches `"security.scan"`, `"security.audit"`, etc. For request/response, use `bus.publish_and_wait(msg, timeout=90)`.

### LLM routing
Every `agent.llm_query()` call goes through `models/model_router.py`:
1. `classify_task(prompt)` — keyword heuristics → `TaskType` enum
2. Primary model for that `TaskType` is tried first
3. On refusal (`is_refusal()` regex match) → try alternative capable models
4. All models refuse → escalate to `OLLAMA_UNCENSORED_MODEL` (dolphin-mistral)
5. All fail → return `"[LLM error: ...]"` string

`is_refusal()` never triggers on responses >600 chars. The GitHub Models endpoint requires model names with publisher prefix: `openai/gpt-4o` not `gpt-4o`.

### NLP intent routing (Telegram)
Free-text messages skip slash commands entirely. `_handle_message()` calls `_classify_intent()` which sends the message + last 6 history turns to the LLM with a structured system prompt, receives `{"intent": "...", "arg": "...", "confidence": 0.0-1.0}`, and dispatches to the matching `_nlp_*` method. Intent handlers are thin wrappers that set `ctx.args` and call the existing `_cmd_*` handlers.

### Tool auto-discovery
`ToolRegistry.discover()` calls `pkgutil.iter_modules` on the `tools/` package directory and imports every module. Any class that subclasses `BaseTool` with a non-default `name` attribute is registered automatically. Import errors are caught and logged but don't abort startup. New tools just need to be added as `.py` files in `tools/`.

### Self-healing git
`self_healing/` handles: versioning (`version_manager.py`), git ops (`git_manager.py`), pre-commit testing (`test_runner.py`), auto-rollback on errors (`rollback_manager.py`), and GitHub bootstrap on startup (`deploy_manager.py`). On startup, `DeployManager.bootstrap()` pulls `GITHUB_BRANCH` and runs smoke tests before making it live.

### Refactor daemon
`refactor_daemon.py` is a standalone long-running process (not started by `main.py`). It cycles through a hardcoded module list, chunks files into ≤280-line segments, sends each to GPT-4o for improvement, syntax-checks the result, applies it, commits, pushes to GitHub, and restarts the bot. Controlled by `REFACTOR_DAEMON_ENABLED` in `.env`.

## Key conventions

### Adding a new agent
1. Create `agents/my_agent.py`, subclass `BaseAgent`
2. Set class attributes `name = "my_agent"` and `description`
3. Implement `async def _register_subscriptions(self)` — call `self._bus.subscribe(topic, handler)`
4. Implement `async def handle(self, description: str, context: str = "") -> str` — required by swarm registry
5. Register in `orchestrator.py` inside `_start_agents()` agent_classes list

### Adding a new Telegram command
1. Add `async def _cmd_mycommand(self, update, ctx)` to `TelegramBot`
2. Start with `if not await self._guard(update, ctx, admin_required=True): return`
3. Register in `_register_handlers()`: `app.add_handler(CommandHandler("mycommand", self._cmd_mycommand))`
4. Add an NLP entry: add `"my_intent": "_nlp_mycommand"` to `_INTENT_MAP` and a `_nlp_mycommand` wrapper method

### Adding a new tool
Create `tools/my_tool.py`:
```python
from tools.base_tool import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "What it does"
    input_schema = {"param": "str"}

    async def execute(self, **kwargs) -> dict:
        ...
        return {"result": ...}
```
No registration needed — auto-discovered on next startup.

### Settings pattern
All config via `config/settings.py` singleton `settings`. Add new env vars using the `_env()`, `_env_bool()`, `_env_int()`, or `_env_list()` helpers. Never import `os.environ` directly in modules — always use `settings.*`.

### Logging
```python
from config.logging_config import get_logger
log = get_logger("agent")   # categories: agent, tool, security, self_improvement, error, combined
log.info("message")
log.bind(agent="my_agent").info("message")  # add structured context
```
Never use `print()` or the stdlib `logging` module in agent/tool code.

## Protected modules

These files must never be auto-patched (enforced by `PROTECTED_MODULES` setting and `refactor_daemon.py`):
- `config/settings.py`
- `config/logging_config.py`
- `sandbox/`
- `self_improvement/`
- `.env`
- `refactor_daemon.py`

## Important gotchas

- **PTB signal handling**: Use `loop.add_signal_handler(sig, handler)` inside an async context. `signal.signal()` conflicts with PTB v21's asyncio event loop and causes silent exit with code 0.
- **Multiple bot instances**: Telegram rejects duplicate `getUpdates` with `telegram.error.Conflict`. Always kill existing `python main.py` processes before restarting.
- **GitHub Models model names**: Require publisher prefix — `openai/gpt-4o`, not `gpt-4o`. Fine-grained PATs do not work with the GitHub Models inference endpoint; use classic PATs.
- **Admin auth**: Configured via `TELEGRAM_ADMIN_USERNAMES` (comma-separated, no `@`) or `TELEGRAM_ADMIN_IDS` (numeric). If neither is set, all users are admins. Users discover their numeric ID by sending `/start`.
- **FAISS optional**: If `faiss-cpu` or `sentence-transformers` are not installed, `VectorStore` falls back to no-embedding mode silently. Bot still functions.
- **System Python**: Project runs on system Python 3.13 (Debian). Install deps with `--break-system-packages`. No virtualenv in use.
