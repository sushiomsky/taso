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
    CODING = "coding"
    ANALYSIS = "analysis"
    SECURITY = "security"
    RESEARCH = "research"
    PLANNING = "planning"
    GENERAL = "general"
    UNCENSORED = "uncensored"  # tasks needing uncensored output


class Provider(str, Enum):
    OLLAMA = "ollama"
    COPILOT = "copilot"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class ModelEntry:
    name: str
    provider: Provider
    capabilities: List[TaskType]
    preferred_tasks: List[TaskType]
    latency_tier: str  # "low" | "medium" | "high"
    cost_tier: str  # "free" | "low" | "medium" | "high"
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
        try:
            self._load_defaults()
            self._apply_overrides()
            log.info(f"ModelRegistry initialized with {len(self._models)} models.")
        except Exception as exc:
            log.error(f"Failed to initialize ModelRegistry: {exc}", exc_info=True)

    def _load_defaults(self) -> None:
        try:
            defaults = self._get_default_models()
            for model in defaults:
                if model.name in self._models:
                    log.warning(f"Duplicate model name '{model.name}' in defaults. Overwriting.")
                self._models[model.name] = model
        except Exception as exc:
            log.error(f"Error loading default models: {exc}", exc_info=True)

    def _get_default_models(self) -> List[ModelEntry]:
        """Return the list of default models."""
        try:
            return [
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
                ModelEntry(
                    name="gpt-4o",
                    provider=Provider.OPENAI,
                    capabilities=list(TaskType),
                    preferred_tasks=[TaskType.ANALYSIS, TaskType.PLANNING],
                    latency_tier="medium", cost_tier="high",
                ),
                ModelEntry(
                    name=settings.OLLAMA_MODEL,
                    provider=Provider.OLLAMA,
                    capabilities=[TaskType.CODING, TaskType.GENERAL, TaskType.RESEARCH,
                                  TaskType.ANALYSIS, TaskType.PLANNING],
                    preferred_tasks=[TaskType.GENERAL, TaskType.CODING],
                    latency_tier="low", cost_tier="free",
                    max_tokens=8192, notes="Local primary Ollama model",
                ),
                ModelEntry(
                    name=settings.OLLAMA_UNCENSORED_MODEL,
                    provider=Provider.OLLAMA,
                    capabilities=list(TaskType),
                    preferred_tasks=[TaskType.UNCENSORED],
                    latency_tier="low", cost_tier="free",
                    max_tokens=8192, uncensored=True,
                    notes="Abliberated local model — used when primary refuses",
                ),
                ModelEntry(
                    name="deepseek-coder",
                    provider=Provider.OLLAMA,
                    capabilities=[TaskType.CODING, TaskType.SECURITY],
                    preferred_tasks=[TaskType.CODING],
                    latency_tier="low", cost_tier="free",
                    max_tokens=8192, notes="Coding specialist — ollama pull deepseek-coder",
                ),
            ]
        except Exception as exc:
            log.error(f"Error generating default models: {exc}", exc_info=True)
            return []

    def _apply_overrides(self) -> None:
        """Load any JSON overrides from MODEL_ROUTING_OVERRIDES env var."""
        raw = settings.MODEL_ROUTING_OVERRIDES
        if not raw:
            return
        try:
            overrides = json.loads(raw)
            for name, patch in overrides.items():
                model = self._models.get(name)
                if model:
                    for key, value in patch.items():
                        if hasattr(model, key):
                            setattr(model, key, value)
                        else:
                            log.warning(f"Invalid override key '{key}' for model '{name}'.")
                    log.info(f"Applied override for model '{name}'.")
                else:
                    log.warning(f"No model found with name '{name}' to apply overrides.")
        except json.JSONDecodeError as exc:
            log.error(f"Failed to parse MODEL_ROUTING_OVERRIDES JSON: {exc}", exc_info=True)
        except Exception as exc:
            log.error(f"Unexpected error while applying overrides: {exc}", exc_info=True)

    def get(self, name: str) -> Optional[ModelEntry]:
        try:
            model = self._models.get(name)
            if not model:
                log.warning(f"Model '{name}' not found.")
            return model
        except Exception as exc:
            log.error(f"Error retrieving model '{name}': {exc}", exc_info=True)
            return None

    def all_models(self) -> List[ModelEntry]:
        try:
            return list(self._models.values())
        except Exception as exc:
            log.error(f"Error retrieving all models: {exc}", exc_info=True)
            return []

    def by_task(self, task_type: TaskType) -> List[ModelEntry]:
        """Return models that support this task, sorted by preference."""
        try:
            models = [
                model for model in self._models.values()
                if task_type in model.capabilities and model.available
            ]
            if not models:
                log.info(f"No available models found for task type '{task_type}'.")
            return models
        except Exception as exc:
            log.error(f"Error retrieving models by task type '{task_type}': {exc}", exc_info=True)
            return []

    def preferred_for(self, task_type: TaskType) -> Optional[ModelEntry]:
        """Return the single best model for a task type."""
        try:
            candidates = [
                model for model in self._models.values()
                if task_type in model.preferred_tasks and model.available
            ]
            if not candidates:
                log.info(f"No preferred models available for task type '{task_type}'.")
            return candidates[0] if candidates else None
        except Exception as exc:
            log.error(f"Error retrieving preferred model for task type '{task_type}': {exc}", exc_info=True)
            return None

    def uncensored_model(self) -> Optional[ModelEntry]:
        """Return the uncensored fallback model."""
        try:
            for model in self._models.values():
                if model.uncensored and model.available:
                    return model
            log.info("No uncensored model available.")
            return None
        except Exception as exc:
            log.error(f"Error retrieving uncensored model: {exc}", exc_info=True)
            return None

    def mark_unavailable(self, name: str) -> None:
        try:
            if name in self._models:
                self._models[name].available = False
                log.warning(f"Model '{name}' marked unavailable.")
            else:
                log.warning(f"Attempted to mark unknown model '{name}' as unavailable.")
        except Exception as exc:
            log.error(f"Error marking model '{name}' as unavailable: {exc}", exc_info=True)

    def mark_available(self, name: str) -> None:
        try:
            if name in self._models:
                self._models[name].available = True
                log.info(f"Model '{name}' marked available.")
            else:
                log.warning(f"Attempted to mark unknown model '{name}' as available.")
        except Exception as exc:
            log.error(f"Error marking model '{name}' as available: {exc}", exc_info=True)

    def status_dict(self) -> Dict:
        try:
            return {name: asdict(model) for name, model in self._models.items()}
        except Exception as exc:
            log.error(f"Error generating status dictionary: {exc}", exc_info=True)
            return {}


registry = ModelRegistry()
