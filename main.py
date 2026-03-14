#!/usr/bin/env python3
"""
TASO – Telegram Autonomous Security Operator
Entry point.

Usage:
    python main.py

Ensure you have copied .env.example to .env and configured at minimum:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_ADMIN_IDS
    LLM_BACKEND (ollama | openai | anthropic)
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent))

from orchestrator import Orchestrator


def main() -> None:
    orchestrator = Orchestrator()
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
