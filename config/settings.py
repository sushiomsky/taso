"""
TASO – Telegram Autonomous Security Operator
Configuration module.

All runtime settings are sourced from environment variables (loaded via
python-dotenv).  A settings singleton is created at import time and
reused everywhere in the application.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Locate the project root and load the .env file
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_list(key: str, default: str = "") -> List[str]:
    raw = _env(key, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Settings dataclass (plain namespace, no external deps)
# ---------------------------------------------------------------------------

class Settings:
    # --- Telegram --------------------------------------------------------
    TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN")
    TELEGRAM_ADMIN_IDS: List[int] = [
        int(x) for x in _env_list("TELEGRAM_ADMIN_IDS") if x.isdigit()
    ]
    # Username-based admin auth (without @); case-insensitive comparison
    TELEGRAM_ADMIN_USERNAMES: List[str] = [
        u.lower().lstrip("@") for u in _env_list("TELEGRAM_ADMIN_USERNAMES")
    ]
    TELEGRAM_ADMIN_CHAT_ID: str = _env("TELEGRAM_ADMIN_CHAT_ID", "")

    # --- LLM backend -----------------------------------------------------
    # Supported backends: "ollama" | "openai" | "anthropic" | "copilot"
    LLM_BACKEND: str = _env("LLM_BACKEND", "ollama")
    OLLAMA_BASE_URL: str = _env("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = _env("OLLAMA_MODEL", "llama3")
    OPENAI_API_KEY: str = _env("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-4o")
    ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    # GitHub Copilot / GitHub Models (OpenAI-compatible)
    GITHUB_TOKEN: str = _env("GITHUB_TOKEN", "")
    COPILOT_API_BASE: str = _env("COPILOT_API_BASE", "https://models.github.ai/inference")
    COPILOT_MODEL: str = _env("COPILOT_MODEL", "openai/gpt-4o")
    # Fallback backend used when primary (e.g. copilot) returns auth errors
    LLM_FALLBACK_BACKEND: str = _env("LLM_FALLBACK_BACKEND", "ollama")

    # --- Uncensored / abliberated local LLM (Ollama) ----------------------
    # Used as last-resort fallback when primary model refuses a request.
    # Recommended: dolphin-mistral, wizard-vicuna-uncensored, nous-hermes
    OLLAMA_UNCENSORED_MODEL: str = _env("OLLAMA_UNCENSORED_MODEL", "dolphin-mistral")
    UNCENSORED_REFUSAL_FALLBACK: bool = _env_bool("UNCENSORED_REFUSAL_FALLBACK", True)

    # --- Swarm system -------------------------------------------------------
    SWARM_MAX_PARALLEL: int = _env_int("SWARM_MAX_PARALLEL", 5)
    SWARM_TASK_TIMEOUT: int = _env_int("SWARM_TASK_TIMEOUT", 120)
    SWARM_ENABLED: bool = _env_bool("SWARM_ENABLED", True)

    # --- Model routing ------------------------------------------------------
    # JSON mapping of task_type → model_name (overrides defaults)
    MODEL_ROUTING_OVERRIDES: str = _env("MODEL_ROUTING_OVERRIDES", "")
    # Runtime disable list for specific model names (comma-separated)
    DISABLED_MODELS: List[str] = _env_list("DISABLED_MODELS", "")

    # --- Database --------------------------------------------------------
    DB_PATH: Path = BASE_DIR / _env("DB_PATH", "data/taso.db")
    VECTOR_INDEX_PATH: Path = BASE_DIR / _env("VECTOR_INDEX_PATH", "data/faiss.index")
    VECTOR_META_PATH: Path = BASE_DIR / _env("VECTOR_META_PATH", "data/faiss_meta.pkl")

    # --- Sandbox / Docker ------------------------------------------------
    DOCKER_SANDBOX_IMAGE: str = _env("DOCKER_SANDBOX_IMAGE", "python:3.11-slim")
    DOCKER_MEM_LIMIT: str = _env("DOCKER_MEM_LIMIT", "256m")
    DOCKER_CPU_QUOTA: int = _env_int("DOCKER_CPU_QUOTA", 50000)   # microseconds per 100ms
    DOCKER_TIMEOUT: int = _env_int("DOCKER_TIMEOUT", 60)           # seconds
    # Allow outbound network from sandbox (bridge). False = fully isolated.
    DOCKER_NETWORK_ENABLED: bool = _env_bool("DOCKER_NETWORK_ENABLED", False)

    # --- Tor / SOCKS proxy -----------------------------------------------
    TOR_SOCKS_HOST: str = _env("TOR_SOCKS_HOST", "127.0.0.1")
    TOR_SOCKS_PORT: int = _env_int("TOR_SOCKS_PORT", 9050)
    TOR_ENABLED: bool = _env_bool("TOR_ENABLED", False)

    # --- Logging ---------------------------------------------------------
    LOG_DIR: Path = BASE_DIR / "logs"
    LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")
    LOG_ROTATION: str = _env("LOG_ROTATION", "50 MB")
    LOG_MONITOR_ENABLED: bool = _env_bool("LOG_MONITOR_ENABLED", False)

    # --- Self-improvement ------------------------------------------------
    SELF_IMPROVE_ENABLED: bool = _env_bool("SELF_IMPROVE_ENABLED", False)
    MAX_PATCH_LINES: int = _env_int("MAX_PATCH_LINES", 500)
    # Modules that are NEVER auto-patched
    PROTECTED_MODULES: List[str] = _env_list(
        "PROTECTED_MODULES",
        "config,sandbox,self_improvement",
    )

    # --- Threat Intel ----------------------------------------------------
    NVD_API_KEY: str = _env("NVD_API_KEY", "")
    NVD_FEED_URL: str = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    CISA_KEV_URL: str = (
        "https://www.cisa.gov/sites/default/files/feeds/"
        "known_exploited_vulnerabilities.json"
    )

    # --- Git -------------------------------------------------------------
    GIT_REPO_PATH: Path = BASE_DIR
    GIT_AUTHOR_NAME: str = _env("GIT_AUTHOR_NAME", "TASO Bot")
    GIT_AUTHOR_EMAIL: str = _env("GIT_AUTHOR_EMAIL", "taso@localhost")

    # --- GitHub auto-deploy --------------------------------------------------
    GITHUB_REPO_URL: str = _env("GITHUB_REPO_URL", "")
    GITHUB_BRANCH: str = _env("GITHUB_BRANCH", "main")
    AUTO_DEPLOY_ON_START: bool = _env_bool("AUTO_DEPLOY_ON_START", False)

    # --- Agent bus -------------------------------------------------------
    BUS_MAX_QUEUE: int = _env_int("BUS_MAX_QUEUE", 1000)
    # Runtime disable list for built-in agents by name (comma-separated)
    DISABLED_AGENTS: List[str] = [
        a.lower().strip() for a in _env_list("DISABLED_AGENTS", "")
    ]
    # Service name used for in-bot config apply/restart operation.
    TASO_SYSTEMD_SERVICE: str = _env("TASO_SYSTEMD_SERVICE", "taso.service")

    # --- Misc ------------------------------------------------------------
    APP_ENV: str = _env("APP_ENV", "development")
    SECRET_KEY: str = _env("SECRET_KEY", os.urandom(32).hex())

    def __repr__(self) -> str:
        return (
            f"<Settings env={self.APP_ENV} llm={self.LLM_BACKEND} "
            f"admins={self.TELEGRAM_ADMIN_IDS} "
            f"admin_users={self.TELEGRAM_ADMIN_USERNAMES}>"
        )


settings = Settings()

# Ensure data dirs exist
settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
