#!/usr/bin/env python3
"""
TASO Example: Dynamic Tool and Agent Creation
==============================================
Demonstrates:
 1. Generating a new tool via DeveloperAgent LLM pipeline
 2. Testing it in sandbox
 3. Registering it in the ToolRegistry
 4. Calling the new tool immediately

 5. Generating a new agent autonomously
 6. Registering it in the swarm

Run with:
    cd /root/taso && python examples/tool_and_agent_creation.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def demo_tool_creation():
    """Generate, test, and register a new tool dynamically."""
    from tools.dynamic_tool_generator import tool_generator
    from tools.sandbox_tester import sandbox_test_tool
    from tools.base_tool import registry as tool_registry
    from memory.audit_log import audit_log

    await audit_log.connect()

    print("\n" + "="*60)
    print("DYNAMIC TOOL CREATION")
    print("="*60)

    task = (
        "Create a tool called 'http_header_checker' that takes a URL "
        "and returns the HTTP response headers as a dict. "
        "Use aiohttp for async requests."
    )

    print(f"\nTask: {task}")
    print("\nGenerating tool via LLM...")

    try:
        tool = await tool_generator.generate(task)
        print(f"  ✅ Generated: {tool.name} (v{tool.version})")
        print(f"  Description: {tool.description}")
        print(f"  Code preview:\n    {tool.code[:200]}...")

        print("\nTesting in sandbox...")
        passed, output = await sandbox_test_tool(tool.code)
        print(f"  {'✅ PASSED' if passed else '❌ FAILED'}: {output[:150]}")

        if passed:
            ok = tool_registry.register_dynamic(
                name=tool.name,
                code=tool.code,
                description=tool.description,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
                tags=tool.tags,
                version=tool.version,
            )
            print(f"  Registry: {'✅ registered' if ok else '⚠️  already exists'}")

            await audit_log.record(
                agent="example_script",
                action="tool_created",
                input_summary=task,
                output_summary=f"Tool '{tool.name}' created, passed={passed}",
                success=passed,
            )

            # Use the tool immediately
            registered = tool_registry.get(tool.name)
            if registered:
                print(f"\n  Calling {tool.name}...")
                result = await registered.run(url="https://httpbin.org/headers")
                print(f"  Result: {result}")

    except Exception as exc:
        print(f"  ❌ Error: {exc}")
        print("  (Requires active LLM backend and network access)")


async def demo_agent_creation():
    """Generate a new agent and register it in the swarm."""
    from agents.message_bus import bus
    from agents.developer_agent import DeveloperAgent
    from memory.audit_log import audit_log

    await audit_log.connect()

    print("\n" + "="*60)
    print("AUTONOMOUS AGENT CREATION")
    print("="*60)

    dev   = DeveloperAgent(bus)
    await dev.start()

    description = (
        "A monitoring agent that watches /var/log/syslog for "
        "kernel error messages and reports them on the monitoring.errors bus topic."
    )

    print(f"\nDescription: {description}")
    print("\nGenerating agent via LLM...")

    result = await dev.create_agent(description, agent_name="syslog_monitor_agent")
    print(f"\n{result}")

    await dev.stop()


async def demo_audit_log():
    """Show the audit log in action."""
    from memory.audit_log import AuditLog
    import tempfile

    print("\n" + "="*60)
    print("AUDIT LOG DEMO")
    print("="*60)

    log = AuditLog(path=Path(tempfile.mkdtemp()) / "demo_audit.db")
    await log.connect()

    # Record some actions
    for i, (agent, action, ok) in enumerate([
        ("security_agent", "code_audit",    True),
        ("developer_agent","tool_created",  True),
        ("self_healing",   "patch_applied", True),
        ("developer_agent","create_agent",  False),
    ]):
        await log.record(
            agent=agent, action=action,
            input_summary=f"input for action {i}",
            output_summary=f"output for action {i}: {'success' if ok else 'failed'}",
            success=ok,
            error=None if ok else "LLM generation failed",
        )

    formatted = await log.format_recent(10)
    print(f"\n{formatted}")

    stats = await log.stats()
    print(f"\nStats: {stats}")


if __name__ == "__main__":
    print("TASO Dynamic Tool & Agent Creation Demo\n")
    asyncio.run(demo_audit_log())
    print("\n(Tool/agent creation requires active LLM — set up .env first)")
