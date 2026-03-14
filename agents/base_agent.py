"""
TASO – Base agent class.

All concrete agents inherit from BaseAgent which provides:
  • lifecycle (start / stop)
  • bus subscription helpers
  • LLM query helper (backend-agnostic)
  • audit logging helper
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from config.logging_config import get_logger
from config.settings import settings
from agents.message_bus import BusMessage, MessageBus

log = get_logger("agent")


class BaseAgent(ABC):
    """Abstract base class for all TASO agents."""

    name: str = "base"
    description: str = ""

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._log = get_logger("agent").bind(agent=self.name)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register subscriptions and begin operation."""
        if self._running:
            self._log.warning(f"Agent '{self.name}' is already running.")
            return
        self._running = True
        try:
            await self._register_subscriptions()
            self._log.info(f"Agent '{self.name}' started.")
        except Exception as exc:
            self._log.error(f"Failed to start agent '{self.name}': {exc}")
            self._running = False
            raise

    async def stop(self) -> None:
        """Cancel all running tasks."""
        if not self._running:
            self._log.warning(f"Agent '{self.name}' is not running.")
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self._log.error(f"Error while stopping task: {result}")
        self._tasks.clear()
        self._log.info(f"Agent '{self.name}' stopped.")

    @abstractmethod
    async def _register_subscriptions(self) -> None:
        """Subscribe to bus topics relevant to this agent."""

    # ------------------------------------------------------------------
    # Publishing helpers
    # ------------------------------------------------------------------

    async def publish(self, topic: str, payload: Dict[str, Any],
                       reply_to: Optional[str] = None,
                       recipient: str = "*") -> None:
        try:
            msg = BusMessage(
                topic=topic,
                sender=self.name,
                payload=payload,
                reply_to=reply_to,
                recipient=recipient,
            )
            await self._bus.publish(msg)
        except Exception as exc:
            self._log.error(f"Failed to publish message to topic '{topic}': {exc}")
            raise

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------

    async def llm_query(self, prompt: str,
                         system: str = "",
                         history: Optional[List[Dict]] = None,
                         task_type: str = "general") -> str:
        """Route through ModelRouter with refusal detection + uncensored fallback."""
        try:
            from models.model_router import router as model_router
            from models.model_registry import TaskType
            tt = TaskType(task_type) if task_type in TaskType._value2member_map_ else TaskType.GENERAL
            return await model_router.query(prompt, system, history, task_type=tt)
        except Exception as exc:
            self._log.error(f"llm_query failed: {exc}")
            return f"[LLM error: {exc}]"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "running": self._running,
            "active_tasks": len(self._tasks),
        }


# ---------------------------------------------------------------------------
# LLM backend helpers
# ---------------------------------------------------------------------------

class _AuthError(Exception):
    """Raised when an LLM backend returns a 401/403 auth error."""


async def _ollama_query(prompt: str, system: str,
                         history: Optional[List[Dict]]) -> str:
    """Query local Ollama server."""
    import aiohttp

    messages = [{"role": "system", "content": system}] if system else []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get("message", {}).get("content", "")
    except aiohttp.ClientError as e:
        log.error(f"Ollama query failed: {e}")
        raise


async def _openai_query(prompt: str, system: str,
                         history: Optional[List[Dict]]) -> str:
    """Query OpenAI API."""
    import openai

    messages = [{"role": "system", "content": system}] if system else []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    try:
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=messages,
            max_tokens=4096,
        )
        return resp.choices[0].message.content
    except Exception as e:
        log.error(f"OpenAI query failed: {e}")
        raise


async def _anthropic_query(prompt: str, system: str,
                            history: Optional[List[Dict]]) -> str:
    """Query Anthropic API."""
    import anthropic

    messages = history or []
    messages.append({"role": "user", "content": prompt})

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system or "You are TASO, an autonomous security research assistant.",
            messages=messages,
        )
        return resp.content[0].text
    except Exception as e:
        log.error(f"Anthropic query failed: {e}")
        raise


async def _copilot_query(prompt: str, system: str,
                          history: Optional[List[Dict]]) -> str:
    """
    Query the GitHub Copilot / GitHub Models API.

    Endpoint priority:
      1. COPILOT_API_BASE (default: https://models.inference.ai.azure.com)
         POST /chat/completions   Authorization: Bearer <GITHUB_TOKEN>
         Requires a classic GitHub PAT with access to GitHub Models beta.

      2. If COPILOT_API_BASE contains 'githubcopilot.com', use the native
         Copilot Chat endpoint with Editor-* headers instead.

    Raises _AuthError on 401/403 so the caller can fall back to Ollama.
    """
    import aiohttp

    if not settings.GITHUB_TOKEN:
        raise _AuthError(
            "GITHUB_TOKEN not set — generate a classic PAT at "
            "https://github.com/settings/tokens"
        )

    messages = [{"role": "system", "content": system}] if system else []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model":       settings.COPILOT_MODEL,
        "messages":    messages,
        "max_tokens":  4096,
        "temperature": 0.2,
    }

    base = settings.COPILOT_API_BASE.rstrip("/")
    url  = f"{base}/chat/completions"

    headers = {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "User-Agent":    "TASO/1.0",
    }

    # Extra headers required for the native Copilot Chat endpoint
    if "githubcopilot.com" in base:
        headers.update({
            "Editor-Version":          "Neovim/0.6.1",
            "Editor-Plugin-Version":   "copilot.vim/1.16.0",
            "Copilot-Integration-Id":  "vscode-chat",
            "OpenAI-Intent":           "conversation-panel",
        })

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status in (401, 403):
                    body = await resp.text()
                    raise _AuthError(
                        f"HTTP {resp.status} from {base} — "
                        f"check GITHUB_TOKEN scope (needs 'models:read' for "
                        f"GitHub Models, or Copilot subscription for Copilot API). "
                        f"Response: {body[:200]}"
                    )
                resp.raise_for_status()
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except aiohttp.ClientError as e:
        log.error(f"Copilot query failed: {e}")
        raise
