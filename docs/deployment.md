# TASO Deployment Guide

## Quick Start (bare metal / VPS)

```bash
git clone https://github.com/sushiomsky/taso.git
cd taso
cp .env.example .env
# Fill in .env: TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, TELEGRAM_ADMIN_USERNAMES
pip install -r requirements.txt
# Optional: install full ML deps for FAISS vector search
pip install faiss-cpu sentence-transformers
# Install and start Ollama (local LLM)
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull dolphin-mistral
python main.py
```

## Docker Compose (recommended)

```bash
git clone https://github.com/sushiomsky/taso.git
cd taso
cp .env.example .env
# Edit .env
docker compose up -d
# Pull LLM model after Ollama starts
docker compose exec ollama ollama pull dolphin-mistral
```

Services started:
- `taso` — main application (auto-restarts)
- `ollama` — local LLM at http://ollama:11434
- `tor` — SOCKS5 proxy at tor:9050 for threat intel crawling

## Environment Variables

See `.env.example` for the complete reference.  Key variables:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | From @BotFather |
| `TELEGRAM_ADMIN_USERNAMES` | ✅ | Comma-separated (no @) |
| `GITHUB_TOKEN` | ✅ for Copilot | Classic PAT from github.com/settings/tokens |
| `LLM_BACKEND` | — | `ollama` \| `openai` \| `copilot` (default: ollama) |
| `OLLAMA_MODEL` | — | Default model (default: llama3) |
| `OLLAMA_UNCENSORED_MODEL` | — | Fallback uncensored (default: dolphin-mistral) |
| `UNCENSORED_REFUSAL_FALLBACK` | — | `true` to enable uncensored fallback |
| `LOG_MONITOR_ENABLED` | — | `true` to enable Telegram error alerts |
| `SELF_IMPROVE_ENABLED` | — | `true` to enable self-improvement loop |
| `SWARM_ENABLED` | — | `true` to enable agent swarm |
| `DOCKER_SANDBOX_IMAGE` | — | Sandbox image (default: python:3.11-slim) |

## Sandbox Setup

The sandbox uses `python:3.11-slim` by default.  Pull it:

```bash
docker pull python:3.11-slim
```

Custom sandbox image (with extra tools):

```bash
cd sandbox/
docker build -f Dockerfile.sandbox -t taso-sandbox:latest .
# Then set in .env:
# DOCKER_SANDBOX_IMAGE=taso-sandbox:latest
```

## Scaling

For higher throughput, increase `SWARM_MAX_PARALLEL` (default: 3):

```env
SWARM_MAX_PARALLEL=6
SWARM_TASK_TIMEOUT=120
```

## GitHub Auto-Push Setup

```env
GITHUB_REPO=https://github.com/your_user/taso.git
GIT_AUTO_PUSH=true
AUTO_DEPLOY_ON_START=false
```

Ensure your PAT has `repo` scope.  TASO will:
1. Commit patches with a version tag
2. Push to `main` branch automatically
3. On restart, optionally pull latest stable version

## Log Monitoring

Logs are written to `logs/agent.log` (loguru rotating, 10MB/file, 7 days).

```bash
# Live tail
tail -f logs/agent.log

# Filter errors only
grep "ERROR\|CRITICAL" logs/agent.log | tail -50
```

The background log monitor (`LOG_MONITOR_ENABLED=true`) sends Telegram
alerts when new errors appear in the log file.

## Updating

```bash
# Via Telegram (recommended)
/dev_deploy

# Manual
git pull origin main
pip install -r requirements.txt
# Restart bot
```

## Security Notes

- Never expose Telegram bot token publicly
- `.env` is gitignored — never commit it
- All generated code runs in Docker sandbox (network=none by default)
- Protected modules cannot be auto-patched:
  `bot/telegram_bot.py`, `config/settings.py`, `sandbox/`, `memory/`
- SOCKS5 proxy required for Tor crawling (disabled by default)
