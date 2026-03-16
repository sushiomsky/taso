#!/usr/bin/env python3
"""
TASO Example: Tool Creation Workflow

Runs the dynamic tool creation demo from tool_and_agent_creation.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Support both `python examples/tool_creation.py` and `python -m examples.tool_creation`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_and_agent_creation import demo_tool_creation


if __name__ == "__main__":
    asyncio.run(demo_tool_creation())
