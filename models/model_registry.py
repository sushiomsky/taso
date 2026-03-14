"""
TASO – Model Registry

Central catalog of available language models: capabilities,
preferred task types, latency tier, and cost tier.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional
from config.logging_config import get_logger
from config.settings import settings

log = get_logger("model_registry")


class TaskType(str, Enum):
    CODING       = "coding"
    ANALYSIS     = "analysis"
    SECURITY     = "security"
    RESEARCH     = "research"
    PLANNING     = "planning"
    GENERAL      = "general"
    UNCENSORED   = "uncensored"   # tasks needing uncensored output


class Provider(str, Enum):
    OLLAMA    = "ollama"
    COPILOT   = "copilot"
    OPENAI    = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class ModelEntry:
    name: str
    provider: Provider
    capabilities: List[TaskType]
    preferred_tasks: List[TaskType]
    latency_tier: str       # "low" | "medium" | "high"
    cost_tier: str          # "free" | "low" | "medium" | "high"
    max_tokens: int = 4096
    uncensored: bool = False
    available: bool = True  # updated by health checks
    notes: str = ""


class ModelRegistry:
    """
    Central registry of all known models.
    Loaded from built-in defaults + optional JSON overrides.
    """
    def __init__(self) -> None:
        self._models: Dict[str, ModelEntry] = {}
        self._load_defaults()
        self._apply_overrides()
        log.info(f"ModelRegistry: {len(self._models)} models registered.")

    def _load_defaults(self) -> None:
        defaults = [
            # ── GitHub Copilot / GitHub Models ───────────────────────────
            ModelEntry(
                name="openai/gpt-4o",
                provider=Provider.COPILOT,
                capabilities=[TaskType.CODING, TaskType.ANALYSIS, TaskType.SECURITY,
                               TaskType.RESEARCH, TaskType.PLANNING, TaskType.GENERAL],
                preferred_tasks=[TaskType.ANALYSIS, TaskType.PLANNING, TaskType.SECURITY],
                latency_tier="medium", cost_tier="low",
                max_tokens=4096, notes="GitHub Models via PAT",
            ),
            ModelEntry(
                name="openai/gpt-4o-mini",
                provider=Provider.COPILOT,
                capabilities=[TaskType.CODING, TaskType.GENERAL, TaskType.RESEARCH],
                preferred_tasks=[TaskType.GENERAL],
                latency_tier="low", cost_tier="free",
                max_tokens=4096, notes="Fast, cheap GitHub Models",
            ),
            # ── OpenAI direct ────────────────────────────────────────────
            ModelEntry(
                name="gpt-4o",
                provider=Provider.OPENAI,
                capabilities=list(TaskType),
                preferred_tasks=[TaskType.ANALYSIS, TaskType.PLANNING],
                latency_tier="medium", cost_tier="high",
            ),
            # ── Ollama local ──────────────────────────────────────────────
            ModelEntry(
                name=settings.OLLAMA_MODEL,
                provider=Provider.OLLAMA,
                capabilities=[TaskType.CODING, TaskType.GENERAL, TaskType.RESEARCH,
                               TaskType.ANALYSIS, TaskType.PLANNING],
                preferred_tasks=[TaskType.GENERAL, TaskType.CODING],
                latency_tier="low", cost_tier="free",
                max_tokens=8192, notes="Local primary Ollama model",
            ),
            # ── Uncensored local LLM ──────────────────────────────────────
            ModelEntry(
                name=settings.OLLAMA_UNCENSORED_MODEL,
                provider=Provider.OLLAMA,
                capabilities=list(TaskType),
                preferred_tasks=[TaskType.UNCENSORED],
                latency_tier="low", cost_tier="free",
                max_tokens=8192, uncensored=True,
                notes="Abliberated local model — used when primary refuses",
            ),
            # ── Coding specialist ─────────────────────────────────────────
            ModelEntry(
                name="deepseek-coder",
                provider=Provider.OLLAMA,
                capabilities=[TaskType.CODING, TaskType.SECURITY],
                preferred_tasks=[TaskType.CODING],
                latency_tier="low", cost_tier="free",
                max_tokens=8192, notes="Coding specialist — ollama pull deepseek-coder",
            ),
        ]
        for m in defaults:
            self._models[m.name] = m

    def _apply_overrides(self) -> None:
        """Load any JSON overrides from MODEL_ROUTING_OVERRIDES env var."""
        raw = settings.MODEL_ROUTING_OVERRIDES
        if not raw:
            return
        try:
            overrides = json.loads(raw)
            for name, patch in overrides.items():
                if name in self._models:
                    for k, v in patch.items():
                        setattr(self._models[name], k, v)
                    log.info(f"ModelRegistry: applied override for '{name}'.")
        except Exception as exc:
            log.warning(f"ModelRegistry: failed to parse MODEL_ROUTING_OVERRIDES: {exc}")

    def get(self, name: str) -> Optional[ModelEntry]:
        return self._models.get(name)

    def all_models(self) -> List[ModelEntry]:
        return list(self._models.values())

    def by_task(self, task_type: TaskType) -> List[ModelEntry]:
        """Return models that support this task, sorted by preference."""
        return [
            m for m in self._models.values()
            if task_type in m.capabilities and m.available
        ]

    def preferred_for(self, task_type: TaskType) -> Optional[ModelEntry]:
        """Return the single best model for a task type."""
        candidates = [
            m for m in self._models.values()
            if task_type in m.preferred_tasks and m.available
        ]
        return candidates[0] if candidates else None

    def uncensored_model(self) -> Optional[ModelEntry]:
        """Return the uncensored fallback model."""
        for m in self._models.values():
            if m.uncensored and m.available:
                return m
        return None

    def mark_unavailable(self, name: str) -> None:
        if name in self._models:
            self._models[name].available = False
            log.warning(f"ModelRegistry: '{name}' marked unavailable.")

    def mark_available(self, name: str) -> None:
        if name in self._models:
            self._models[name].available = True

    def status_dict(self) -> Dict:
        return {name: asdict(m) for name, m in self._models.items()}


registry = ModelRegistry()
