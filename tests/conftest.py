"""
TASO – pytest configuration and shared fixtures.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio

# Ensure project root is importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Use a throwaway SQLite DB for every test run
os.environ.setdefault("DB_PATH",         str(ROOT / "data" / "test_taso.db"))
os.environ.setdefault("CONVERSATIONS_DB", str(ROOT / "data" / "test_conversations.db"))
os.environ.setdefault("VERSION_HISTORY_DB", str(ROOT / "data" / "test_version_history.db"))
os.environ.setdefault("LLM_BACKEND",     "ollama")   # no real API calls in tests
os.environ.setdefault("SWARM_ENABLED",   "false")
os.environ.setdefault("TOR_ENABLED",     "false")
os.environ.setdefault("SELF_IMPROVE_ENABLED", "false")
os.environ.setdefault("AUTO_DEPLOY_ON_START", "false")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test_token")
os.environ.setdefault("TELEGRAM_ADMIN_USERNAMES", "testadmin")


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    """Temporary directory, cleaned up after each test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
async def message_bus():
    """Start a MessageBus, yield it, then stop it."""
    from agents.message_bus import MessageBus
    bus = MessageBus()
    await bus.start()
    yield bus
    await bus.stop()
