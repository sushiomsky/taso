"""
TASO – Model Router

Routes each query to the best available model by task type with refusal-aware
fallbacks.

Fallback order:
  1) Primary model for task/backend
  2) Other capable models
  3) Uncensored local model (if enabled)
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Sequence

import aiohttp

from config.logging_config import get_logger
from config.settings import settings
from models.model_registry import ModelEntry, ModelRegistry, Provider, TaskType, registry
from models.ollama_client import is_refusal, ollama_chat

log = get_logger("model_router")


# ---------------------------------------------------------------------------
# Task classifier
# ---------------------------------------------------------------------------

_TASK_KEYWORDS: Dict[TaskType, List[str]] = {
    TaskType.CODING: [
        "code",
        "function",
        "class",
        "bug",
        "fix",
        "implement",
        "python",
        "javascript",
        "script",
        "program",
        "def ",
        "import ",
    ],
    TaskType.SECURITY: [
        "vulnerability",
        "cve",
        "exploit",
        "malware",
        "pentest",
        "attack",
        "payload",
        "injection",
        "xss",
        "sqli",
        "audit",
    ],
    TaskType.RESEARCH: [
        "research",
        "find",
        "gather",
        "collect",
        "sources",
        "information",
        "explain",
        "what is",
        "how does",
    ],
    TaskType.ANALYSIS: [
        "analyze",
        "analyse",
        "evaluate",
        "assess",
        "review",
        "compare",
        "summarize",
        "report",
        "findings",
    ],
    TaskType.PLANNING: [
        "plan",
        "steps",
        "breakdown",
        "workflow",
        "strategy",
        "design",
        "architect",
        "organize",
        "schedule",
    ],
}


def classify_task(prompt: str) -> TaskType:
    """Classify a prompt into a TaskType using keyword heuristics."""
    text = prompt.lower()
    scores: Dict[TaskType, int] = {task_type: 0 for task_type in TaskType}
    for task_type, keywords in _TASK_KEYWORDS.items():
        scores[task_type] = sum(1 for keyword in keywords if keyword in text)

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else TaskType.GENERAL


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ModelRouter:
    """Selects models and executes fallback logic."""

    def __init__(self, reg: ModelRegistry = registry) -> None:
        self._reg = reg

    async def query(
        self,
        prompt: str,
        system: str = "",
        history: Optional[Sequence[dict]] = None,
        task_type: Optional[TaskType] = None,
        force_model: Optional[str] = None,
        allow_uncensored: bool = True,
    ) -> str:
        """Route a query to the best model and return text output."""
        resolved_task = task_type or classify_task(prompt)
        normalized_history = _normalize_history(history)

        try:
            if force_model:
                forced_entry = self._reg.get(force_model)
                if not forced_entry:
                    return f"[ModelRouter: model '{force_model}' is not registered.]"
                forced_result = await self._try_model(
                    forced_entry, prompt, system, normalized_history
                )
                if forced_result:
                    return forced_result
                return f"[ModelRouter: model '{force_model}' is unavailable or failed.]"

            return await self._routed_query(
                prompt=prompt,
                system=system,
                history=normalized_history,
                task_type=resolved_task,
                allow_uncensored=allow_uncensored,
            )
        except Exception as exc:
            log.exception(f"ModelRouter: unexpected query error: {exc}")
            return "[ModelRouter: unexpected routing error.]"

    async def _routed_query(
        self,
        prompt: str,
        system: str,
        history: List[Dict[str, str]],
        task_type: TaskType,
        allow_uncensored: bool,
    ) -> str:
        tried: List[str] = []

        primary = self._get_primary_model(task_type)
        if primary:
            response = await self._try_model(primary, prompt, system, history)
            if response and not is_refusal(response):
                return response
            if response and is_refusal(response):
                log.warning(
                    f"ModelRouter: primary '{primary.name}' refused for task={task_type.value}"
                )
            tried.append(primary.name)

        for candidate in self._reg.by_task(task_type):
            if candidate.name in tried or candidate.uncensored:
                continue
            response = await self._try_model(candidate, prompt, system, history)
            if response and not is_refusal(response):
                return response
            tried.append(candidate.name)

        if allow_uncensored and settings.UNCENSORED_REFUSAL_FALLBACK:
            uncensored = self._reg.uncensored_model()
            if uncensored and uncensored.name not in tried:
                log.warning(
                    f"ModelRouter: escalating to uncensored model '{uncensored.name}' "
                    f"for task={task_type.value}"
                )
                response = await self._try_model(uncensored, prompt, system, history)
                if response:
                    return response

        return (
            "[ModelRouter: all models failed or refused. Check provider availability "
            "and configured credentials.]"
        )

    def _get_primary_model(self, task_type: TaskType) -> Optional[ModelEntry]:
        """Resolve the primary model for a task type and active backend."""
        # Keep chat-first uncensored behavior only for Ollama backend;
        # other backends should honor their configured primary model.
        if task_type == TaskType.GENERAL and settings.LLM_BACKEND == "ollama":
            uncensored = self._reg.uncensored_model()
            if uncensored and uncensored.available:
                return uncensored

        backend_map = {
            "copilot": settings.COPILOT_MODEL,
            "ollama": settings.OLLAMA_MODEL,
            "openai": settings.OPENAI_MODEL,
            "anthropic": settings.ANTHROPIC_MODEL,
        }
        configured_name = backend_map.get(settings.LLM_BACKEND)
        if configured_name:
            configured_entry = self._reg.get(configured_name)
            if configured_entry and configured_entry.available:
                return configured_entry

        return self._reg.preferred_for(task_type)

    async def _try_model(
        self,
        entry: ModelEntry,
        prompt: str,
        system: str,
        history: List[Dict[str, str]],
    ) -> Optional[str]:
        """Attempt one provider call; return None for fallback on failure."""
        try:
            return await self._call_provider(entry, prompt, system, history)
        except asyncio.TimeoutError:
            log.warning(f"ModelRouter: timeout from '{entry.name}'")
            self._reg.mark_unavailable(entry.name)
            return None
        except PermissionError as exc:
            log.warning(f"ModelRouter: auth/permission error from '{entry.name}': {exc}")
            return None
        except aiohttp.ClientError as exc:
            log.warning(f"ModelRouter: network/client error from '{entry.name}': {exc}")
            self._reg.mark_unavailable(entry.name)
            return None
        except Exception as exc:
            log.warning(f"ModelRouter: provider error from '{entry.name}': {exc}")
            message = str(exc).lower()
            if any(token in message for token in ("connect", "timeout", "refused", "not installed")):
                self._reg.mark_unavailable(entry.name)
            return None

    async def _call_provider(
        self,
        entry: ModelEntry,
        prompt: str,
        system: str,
        history: List[Dict[str, str]],
    ) -> str:
        if entry.provider == Provider.OLLAMA:
            return await ollama_chat(
                model=entry.name,
                prompt=prompt,
                system=system,
                history=history,
                timeout=180,
            )

        if entry.provider == Provider.COPILOT:
            return await _copilot_call(entry.name, prompt, system, history)

        if entry.provider == Provider.OPENAI:
            return await _openai_call(entry.name, prompt, system, history)

        if entry.provider == Provider.ANTHROPIC:
            return await _anthropic_call(entry.name, prompt, system, history)

        raise ValueError(f"Unsupported provider: {entry.provider}")

    def status(self) -> Dict[str, object]:
        """Return router status for bot/system commands."""
        general_primary = self._get_primary_model(TaskType.GENERAL)
        return {
            "active_backend": settings.LLM_BACKEND,
            "primary_model": general_primary.name if general_primary else "None",
            "uncensored_model": settings.OLLAMA_UNCENSORED_MODEL,
            "uncensored_fallback_enabled": settings.UNCENSORED_REFUSAL_FALLBACK,
            "registered_models": len(self._reg.all_models()),
        }


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------


def _normalize_history(history: Optional[Sequence[dict]]) -> List[Dict[str, str]]:
    if not history:
        return []

    normalized: List[Dict[str, str]] = []
    for message in history:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        normalized.append({"role": role, "content": content})
    return normalized


def _build_messages(
    prompt: str,
    system: str,
    history: Optional[Sequence[dict]],
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(_normalize_history(history))
    messages.append({"role": "user", "content": prompt})
    return messages


def _build_copilot_headers() -> Dict[str, str]:
    if not settings.GITHUB_TOKEN:
        raise PermissionError("GITHUB_TOKEN is not configured")

    headers = {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "TASO/1.0",
    }

    base = settings.COPILOT_API_BASE.rstrip("/")
    if "githubcopilot.com" in base:
        headers.update(
            {
                "Editor-Version": "Neovim/0.6.1",
                "Editor-Plugin-Version": "copilot.vim/1.16.0",
                "Copilot-Integration-Id": "vscode-chat",
                "OpenAI-Intent": "conversation-panel",
            }
        )

    return headers


async def _copilot_call(
    model: str,
    prompt: str,
    system: str,
    history: Optional[Sequence[dict]],
) -> str:
    messages = _build_messages(prompt, system, history)
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.2,
    }

    base = settings.COPILOT_API_BASE.rstrip("/")
    async with aiohttp.ClientSession(headers=_build_copilot_headers()) as session:
        async with session.post(
            f"{base}/chat/completions",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as response:
            if response.status in (401, 403):
                body = await response.text()
                raise PermissionError(f"Copilot auth failed ({response.status}): {body[:120]}")
            response.raise_for_status()
            data = await response.json()

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("Copilot response did not contain message content")
    return content


async def _openai_call(
    model: str,
    prompt: str,
    system: str,
    history: Optional[Sequence[dict]],
) -> str:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is not installed") from exc

    if not settings.OPENAI_API_KEY:
        raise PermissionError("OPENAI_API_KEY is not configured")

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    messages = _build_messages(prompt, system, history)
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=4096,
    )

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RuntimeError("OpenAI response did not contain message content")
    return content


async def _anthropic_call(
    model: str,
    prompt: str,
    system: str,
    history: Optional[Sequence[dict]],
) -> str:
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package is not installed") from exc

    if not settings.ANTHROPIC_API_KEY:
        raise PermissionError("ANTHROPIC_API_KEY is not configured")

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    anthropic_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in _build_messages(prompt, "", history)
        if message["role"] in ("user", "assistant")
    ]

    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system or "You are TASO, an autonomous security research assistant.",
        messages=anthropic_messages,
    )

    parts = [getattr(block, "text", "") for block in response.content if getattr(block, "text", "")]
    content = "".join(parts).strip()
    if not content:
        raise RuntimeError("Anthropic response did not contain text content")
    return content


# Module singleton expected by the rest of the codebase.
router = ModelRouter()
