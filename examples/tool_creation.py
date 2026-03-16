#!/usr/bin/env python3
"""
TASO Example: Tool Creation Workflow

Runs the dynamic tool creation demo from tool_and_agent_creation.py.
"""
from __future__ import annotations

import asyncio

from tool_and_agent_creation import demo_tool_creation


if __name__ == "__main__":
    asyncio.run(demo_tool_creation())
