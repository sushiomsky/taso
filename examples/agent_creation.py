#!/usr/bin/env python3
"""
TASO Example: Agent Creation Workflow

Runs the autonomous agent creation demo from tool_and_agent_creation.py.
"""
from __future__ import annotations

import asyncio

from tool_and_agent_creation import demo_agent_creation


if __name__ == "__main__":
    asyncio.run(demo_agent_creation())
