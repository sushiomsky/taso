"""
TASO – Model Router

Routes each LLM query to the best available model for the task type.

Fallback chain:
  1. Primary model for task_type
  2. Alternative capable model
  3. Local Ollama default
  4. Uncensored local LLM (when primary refuses or UNCENSORED task type)

All fallbacks are logged to the audit trail.
"""
from __future__ import annotations
import asyncio
from typing import Dict, List, Optional
from config.logging_config import get_logger
from config.settings import settings
from models.model_registry import ModelRegistry, ModelEntry, TaskType, Provider, registry
from models.ollama_client import ollama_chat, is_refusal

log = get_logger("model_router")


# ---------------------------------------------------------------------------
# Task classifier – keyword-based heuristic
# ---------------------------------------------------------------------------

_TASK_KEYWORDS: Dict[TaskType, List[str]] = {
    TaskType.CODING:    ["code", "function", "class", "bug", "fix", "implement",
                         "python", "javascript", "script", "program", "def ", "import "],
    TaskType.SECURITY:  ["vulnerability", "cve", "exploit", "malware", "pentest",
                         "attack", "payload", "injection", "xss", "sqli", "audit"],
    TaskType.RESEARCH:  ["research", "find", "gather", "collect", "sources",
                         "information", "explain", "what is", "how does"],
    TaskType.ANALYSIS:  ["analyze", "analyse", "evaluate", "assess", "review",
                         "compare", "summarize", "report", "findings"],
    TaskType.PLANNING:  ["plan", "steps", "breakdown", "workflow", "strategy",
                         "design", "architect", "organize", "schedule"],
}


def classify_task(prompt: str) -> TaskType:
    """Classify a prompt into a TaskType using keyword heuristics."""
    text = prompt.lower()
    scores: Dict[TaskType, int] = {t: 0 for t in TaskType}
    for task_type, keywords in _TASK_KEYWORDS.items():
        scores[task_type] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=lambda t: scores[t])
    return best if scores[best] > 0 else TaskType.GENERAL


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------

class ModelRouter:
    """
    Selects a model for each LLM call, executes it, and handles:
      • refusal detection
      • automatic fallback to alternative models
      • final fallback to uncensored local LLM
    """

    def __init__(self, reg: ModelRegistry = registry) -> None:
        self._reg = reg

    async def query(
        self,
        prompt: str,
        system: str = "",
        history: Optional[List[Dict]] = None,
        task_type: Optional[TaskType] = None,
        force_model: Optional[str] = None,
        allow_uncensored: bool = True,
    ) -> str:
        """
        Route the query to the best model.
        Returns the text response.
        """
        try:
            if task_type is None:
                task_type = classify_task(prompt)

            if force_model:
                entry = self._reg.get(force_model)
                if entry:
                    return await self._call_model(entry, prompt, system, history)

            return await self._routed_query(prompt, system, history, task_type, allow_uncensored)
        except Exception as exc:
            log.exception("ModelRouter: Unexpected error during query routing")
            return "[ModelRouter: An unexpected error occurred. Please try again later.]"

    async def _routed_query(
        self,
        prompt: str,
        system: str,
        history: Optional[List],
        task_type: TaskType,
        allow_uncensored: bool,
    ) -> str:
        """Try primary → alternatives → uncensored in order."""
        tried: List[str] = []

        # 1. Primary model for task type
        primary = self._get_primary_model(task_type)
        if primary:
            result = await self._try_model(primary, prompt, system, history)
            if result is not None and not is_refusal(result):
                return result
            if result and is_refusal(result):
                log.warning(
                    f"ModelRouter: '{primary.name}' refused for {task_type.value}. "
                    f"Trying fallbacks."
                )
            tried.append(primary.name)

        # 2. Alternative capable models
        for alt in self._reg.by_task(task_type):
            if alt.name in tried or alt.uncensored:
                continue
            result = await self._try_model(alt, prompt, system, history)
            if result is not None and not is_refusal(result):
                return result
            tried.append(alt.name)

        # 3. Uncensored fallback
        if allow_uncensored and settings.UNCENSORED_REFUSAL_FALLBACK:
            uncensored = self._reg.uncensored_model()
            if uncensored and uncensored.name not in tried:
                log.warning(
                    f"ModelRouter: all models refused/failed for {task_type.value}. "
                    f"Escalating to uncensored model '{uncensored.name}'."
                )
                result = await self._try_model(uncensored, prompt, system, history)
                if result is not None:
                    return result

        return "[ModelRouter: All models failed or refused. Ensure the backend services are running and models are available.]"

    def _get_primary_model(self, task_type: TaskType) -> Optional[ModelEntry]:
        """Get the configured primary model for the active LLM backend + task type."""
        backend = settings.LLM_BACKEND

        try:
            if backend == "copilot":
                entry = self._reg.get(settings.COPILOT_MODEL)
                if entry and entry.available:
                    return entry
            elif backend == "ollama":
                entry = self._reg.get(settings.OLLAMA_MODEL)
                if entry and entry.available:
                    return entry
            elif backend == "openai":
                entry = self._reg.get(settings.OPENAI_MODEL)
                if entry and entry.available:
                    return entry

            # Fall back to registry preferred model for task
            return self._reg.preferred_for(task_type)
        except Exception as exc:
            log.exception(f"ModelRouter: Error retrieving primary model for {task_type.value}")
            return None

    async def _try_model(
        self,
        entry: ModelEntry,
        prompt: str,
        system: str,
        history: Optional[List],
    ) -> Optional[str]:
        """
        Call a model. Returns None on connection/auth error (allows fallback).
        Returns the text (even if it's a refusal) on success.
        """
        try:
            if entry.provider == Provider.OLLAMA:
                return await ollama_chat(entry.name, prompt, system, history)
            elif entry.provider == Provider.COPILOT:
                return await _copilot_call(entry.name, prompt, system, history)
            elif entry.provider == Provider.OPENAI:
                return await _openai_call(entry.name, prompt, system, history)
            else:
                log.error(f"ModelRouter: Unsupported provider '{entry.provider}' for model '{entry.name}'")
                return None
        except asyncio.TimeoutError:
            log.warning(f"ModelRouter: Timeout while querying model '{entry.name}'")
            self._reg.mark_unavailable(entry.name)
            return None
        except Exception as exc:
            log.warning(f"ModelRouter: Error querying model '{entry.name}': {exc}")
            if "connect" in str(exc).lower() or "timeout" in str(exc).lower():
                self._reg.mark_unavailable(entry.name)
            return None

    async def _call_model(
        self,
        entry: ModelEntry,
        prompt: str,
        system: str,
        history: Optional[List],
    ) -> str:
        result = await self._try_model(entry, prompt, system, history)
        if result is not None:
            return result
        return f"[ModelRouter: Model '{entry.name}' is unavailable or returned an error.]"

    def status(self) -> Dict:
        try:
            return {
                "active_backend": settings.LLM_BACKEND,
                "primary_model": settings.COPILOT_MODEL if settings.LLM_BACKEND == "copilot" else settings.OLLAMA_MODEL,
                "uncensored_model": settings.OLLAMA_UNCENSORED_MODEL,
                "uncensored_fallback_enabled": settings.UNCENSORED_REFUSAL_FALLBACK,
                "registered_models": len(self._reg.all_models()),
            }
        except Exception as exc:
            log.exception("ModelRouter: Error retrieving status")
            return {"error": "Unable to retrieve status."}


# ---------------------------------------------------------------------------
# Backend call helpers
# ---------------------------------------------------------------------------

async def _copilot_call(model: str, prompt: str, system: str, history) -> str:
    import aiohttp
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "TASO/1.0",
    }
    base = settings.COPILOT_API_BASE.rstrip("/")
    if "githubcopilot.com" in base:
        headers.update({
            "Editor-Version": "Neovim/0.6.1",
            "Copilot-Integration-Id": "vscode-chat",
        })

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(
                f"{base}/chat/completions",
                json={"model": model, "messages": messages, "max_tokens": 4096, "temperature": 0.2},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status in (401, 403):
                    raise PermissionError(f"Auth error {resp.status}")
                resp.raise_for_status()
                data = await resp.json()
            return data["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            log.warning(f"Copilot API call timed out for model '{model}'")
            raise
        except aiohttp.ClientError as e:
            log.error(f"Copilot API call failed: {e}")
            raise

async def _openai_call(model: str, prompt: str, system: str, history) -> str:
    import openai
    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    try:
        resp = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=4096
        )
        return resp.choices[0].message.content
    except asyncio.TimeoutError:
        log.warning(f"OpenAI API call timed out for model '{model}'")
        raise
    except openai.error.OpenAIError as e:
        log.error(f"OpenAI API call failed: {e}")
        raise


# Singleton router
router = ModelRouter()
