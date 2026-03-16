#!/usr/bin/env python3
"""
TASO Example: Agent Creation Workflow

Runs the autonomous agent creation demo from tool_and_agent_creation.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Support both `python examples/agent_creation.py` and `python -m examples.agent_creation`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_and_agent_creation import demo_agent_creation


if __name__ == "__main__":
    asyncio.run(demo_agent_creation())
