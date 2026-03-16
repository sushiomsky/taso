"""
TASO runtime configuration manager.

Provides a safe, bot-friendly interface to read/write a curated subset of .env
configuration values for feature flags, model routing choices, and built-in
agent enable/disable controls.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import dotenv_values, set_key

from config.logging_config import get_logger
from config.settings import BASE_DIR, settings

log = get_logger("agent")


class RuntimeConfigManager:
    """Manage runtime-editable TASO settings persisted in `.env`."""

    FEATURE_ENV_KEYS: Dict[str, str] = {
        "self_improve": "SELF_IMPROVE_ENABLED",
        "swarm": "SWARM_ENABLED",
        "log_monitor": "LOG_MONITOR_ENABLED",
        "tor": "TOR_ENABLED",
        "auto_deploy": "AUTO_DEPLOY_ON_START",
        "uncensored_fallback": "UNCENSORED_REFUSAL_FALLBACK",
    }

    MODEL_SLOT_ENV_KEYS: Dict[str, str] = {
        "ollama": "OLLAMA_MODEL",
        "openai": "OPENAI_MODEL",
        "anthropic": "ANTHROPIC_MODEL",
        "copilot": "COPILOT_MODEL",
        "uncensored": "OLLAMA_UNCENSORED_MODEL",
    }

    VALID_BACKENDS = {"ollama", "openai", "anthropic", "copilot"}

    BUILTIN_AGENTS = [
        "coordinator",
        "security",
        "research",
        "dev",
        "memory",
        "system",
        "planner",
        "coder",
        "analysis",
        "developer",
        "self_healing",
        "monitoring",
    ]

    AGENT_ALIASES = {
        "self-healing": "self_healing",
        "selfhealing": "self_healing",
        "security_agent": "security",
        "research_agent": "research",
        "memory_agent": "memory",
        "system_agent": "system",
        "dev_agent": "dev",
        "developer_agent": "developer",
        "coordinator_agent": "coordinator",
        "monitoring_agent": "monitoring",
    }

    PROTECTED_AGENTS = {"coordinator"}

    def __init__(self, env_path: Optional[Path] = None) -> None:
        self._env_path = Path(env_path or (BASE_DIR / ".env"))
        self._env_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def env_path(self) -> Path:
        return self._env_path

    def _ensure_env_file(self) -> None:
        if not self._env_path.exists():
            self._env_path.touch()

    def _read_env(self) -> Dict[str, str]:
        self._ensure_env_file()
        raw = dotenv_values(self._env_path)
        return {k: (str(v) if v is not None else "") for k, v in raw.items()}

    @staticmethod
    def _parse_csv(raw: str) -> List[str]:
        return [x.strip() for x in str(raw or "").split(",") if x.strip()]

    @staticmethod
    def _is_truthy(raw: Any) -> bool:
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}

    @staticmethod
    def _normalize_bool(enabled: bool) -> str:
        return "true" if enabled else "false"

    def _read_key(self, key: str, default: str = "") -> str:
        env = self._read_env()
        if key in env and env[key] != "":
            return env[key]

        if hasattr(settings, key):
            value = getattr(settings, key)
            if isinstance(value, bool):
                return self._normalize_bool(value)
            if isinstance(value, list):
                return ",".join(str(v) for v in value if str(v).strip())
            return str(value)

        return default

    def _write_key(self, key: str, value: str) -> None:
        self._ensure_env_file()
        set_key(str(self._env_path), key, str(value), quote_mode="never")

    def _write_csv(self, key: str, items: List[str]) -> None:
        self._write_key(key, ",".join(items))

    def feature_names(self) -> List[str]:
        return sorted(self.FEATURE_ENV_KEYS.keys())

    def feature_status(self) -> Dict[str, bool]:
        status: Dict[str, bool] = {}
        for feature_name, env_key in self.FEATURE_ENV_KEYS.items():
            status[feature_name] = self._is_truthy(self._read_key(env_key))
        return status

    def set_feature_enabled(self, feature_name: str, enabled: bool) -> Tuple[bool, str]:
        feature_key = feature_name.lower().strip()
        env_key = self.FEATURE_ENV_KEYS.get(feature_key)
        if not env_key:
            return (
                False,
                f"Unknown feature '{feature_name}'. Available: {', '.join(self.feature_names())}",
            )
        self._write_key(env_key, self._normalize_bool(enabled))
        return True, env_key

    def _resolve_agent_name(self, agent_name: str) -> Optional[str]:
        normalized = agent_name.strip().lower().replace("-", "_")
        if normalized.endswith("_agent"):
            normalized = normalized[: -len("_agent")]
        normalized = self.AGENT_ALIASES.get(normalized, normalized)
        return normalized if normalized in self.BUILTIN_AGENTS else None

    def disabled_agents(self) -> List[str]:
        raw = self._read_key("DISABLED_AGENTS")
        return [a.lower() for a in self._parse_csv(raw)]

    def set_agent_enabled(self, agent_name: str, enabled: bool) -> Tuple[bool, str]:
        resolved = self._resolve_agent_name(agent_name)
        if not resolved:
            return (
                False,
                "Unknown agent. Available: " + ", ".join(self.BUILTIN_AGENTS),
            )
        if not enabled and resolved in self.PROTECTED_AGENTS:
            return False, f"Agent '{resolved}' is required and cannot be disabled."

        disabled = self.disabled_agents()
        disabled = [a for a in disabled if a != resolved]
        if not enabled:
            disabled.append(resolved)
        self._write_csv("DISABLED_AGENTS", sorted(set(disabled)))
        return True, resolved

    def model_status(self) -> Dict[str, Any]:
        slots = {
            slot: self._read_key(env_key)
            for slot, env_key in self.MODEL_SLOT_ENV_KEYS.items()
        }
        disabled = self.disabled_models()
        return {
            "backend": self._read_key("LLM_BACKEND", settings.LLM_BACKEND),
            "slots": slots,
            "disabled": disabled,
        }

    def disabled_models(self) -> List[str]:
        raw = self._read_key("DISABLED_MODELS")
        return self._parse_csv(raw)

    def set_model_enabled(self, model_name: str, enabled: bool) -> Tuple[bool, str]:
        model = model_name.strip()
        if not model:
            return False, "Model name cannot be empty."

        disabled = self.disabled_models()
        filtered = [m for m in disabled if m.lower() != model.lower()]
        if not enabled:
            filtered.append(model)
        self._write_csv("DISABLED_MODELS", filtered)
        return True, model

    def set_backend(self, backend: str) -> Tuple[bool, str]:
        selected = backend.strip().lower()
        if selected not in self.VALID_BACKENDS:
            return (
                False,
                f"Invalid backend '{backend}'. Valid: {', '.join(sorted(self.VALID_BACKENDS))}",
            )
        self._write_key("LLM_BACKEND", selected)
        return True, selected

    def set_model_slot(self, slot: str, model_name: str) -> Tuple[bool, str]:
        slot_key = slot.strip().lower()
        env_key = self.MODEL_SLOT_ENV_KEYS.get(slot_key)
        if not env_key:
            return (
                False,
                f"Unknown slot '{slot}'. Valid: {', '.join(sorted(self.MODEL_SLOT_ENV_KEYS.keys()))}",
            )
        value = model_name.strip()
        if not value:
            return False, "Model name cannot be empty."
        self._write_key(env_key, value)
        return True, env_key

    def systemd_service_name(self) -> str:
        raw = self._read_key("TASO_SYSTEMD_SERVICE", settings.TASO_SYSTEMD_SERVICE)
        service = raw.strip() or "taso.service"
        return service if service.endswith(".service") else f"{service}.service"

    def snapshot(self) -> Dict[str, Any]:
        feature_status = self.feature_status()
        disabled_agents = set(self.disabled_agents())
        return {
            "features": feature_status,
            "agents": {
                "enabled": [a for a in self.BUILTIN_AGENTS if a not in disabled_agents],
                "disabled": [a for a in self.BUILTIN_AGENTS if a in disabled_agents],
            },
            "models": self.model_status(),
            "restart_service": self.systemd_service_name(),
        }


runtime_config_manager = RuntimeConfigManager()

