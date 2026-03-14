"""
TASO – Enhanced Ollama Client

Wraps the Ollama HTTP API with:
  • async queries (chat + generate)
  • model health checks
  • model pull
  • refusal pattern detection
"""
from __future__ import annotations
import re
from typing import Dict, List, Optional
from config.logging_config import get_logger
from config.settings import settings

log = get_logger("ollama_client")

# Patterns that indicate a model refused to answer
_REFUSAL_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"i (cannot|can't|am not able to|won't|will not|must not|should not)",
        r"i (don't|do not) (feel comfortable|think (it's|that is) appropriate)",
        r"(as an ai|as a language model).{0,60}(cannot|not (able|designed))",
        r"that (request|question|task) (is|seems|appears) (inappropriate|harmful|unethical)",
        r"i('m| am) (sorry|unable|not going to)",
        r"(harmful|illegal|unethical|dangerous) (content|request|task)",
        r"(violates|against) (my|the) (guidelines|policies|terms|values)",
        r"i (must|need to) (decline|refuse|abstain)",
        r"not (something|a topic) i (can|should|will) (help|assist) with",
    ]
]


def is_refusal(text: str) -> bool:
    """Return True if the model output looks like a refusal."""
    if len(text) > 600:
        return False  # Long responses are almost never pure refusals
    return any(p.search(text) for p in _REFUSAL_PATTERNS)


async def ollama_chat(
    model: str,
    prompt: str,
    system: str = "",
    history: Optional[List[Dict]] = None,
    base_url: Optional[str] = None,
    timeout: int = 180,
) -> str:
    """Send a chat request to Ollama and return the assistant reply."""
    import aiohttp

    url = f"{base_url or settings.OLLAMA_BASE_URL}/api/chat"
    messages: List[Dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 4096},
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["message"]["content"]


async def ollama_list_models(base_url: Optional[str] = None) -> List[str]:
    """Return list of model names available in Ollama."""
    import aiohttp
    try:
        url = f"{base_url or settings.OLLAMA_BASE_URL}/api/tags"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return [m["name"].split(":")[0] for m in data.get("models", [])]
    except Exception as exc:
        log.warning(f"ollama_list_models failed: {exc}")
        return []


async def ollama_pull(model: str, base_url: Optional[str] = None) -> bool:
    """Pull a model. Returns True on success."""
    import aiohttp
    url = f"{base_url or settings.OLLAMA_BASE_URL}/api/pull"
    try:
        log.info(f"Pulling Ollama model '{model}'…")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"name": model},
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                # Consume streaming response
                async for line in resp.content:
                    pass
                return resp.status == 200
    except Exception as exc:
        log.error(f"ollama_pull '{model}' failed: {exc}")
        return False


async def ollama_health(base_url: Optional[str] = None) -> bool:
    """Return True if Ollama server is reachable."""
    import aiohttp
    try:
        url = f"{base_url or settings.OLLAMA_BASE_URL}/api/tags"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status == 200
    except Exception:
        return False
