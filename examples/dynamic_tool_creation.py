"""
TASO – Dynamic Tool Creation Example

Demonstrates the complete pipeline:
  1. PlannerAgent detects a task that needs a missing tool
  2. DeveloperAgent generates the tool via LLM
  3. SecurityAgent tests it in a sandboxed subprocess
  4. ToolRegistry registers the tool (auto-persisted to disk)
  5. MemoryAgent stores metadata in vector + structured DB
  6. All agents can immediately call the new tool

Run this script from the project root:
    python examples/dynamic_tool_creation.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# 1. Low-level: direct ToolRegistry + sandbox_tester
# ---------------------------------------------------------------------------

async def demo_direct_registration() -> None:
    """
    Show register_dynamic(), call_tool(), and persistence without agents.
    Uses a hand-crafted run_tool() so no LLM is needed.
    """
    print("\n=== Demo 1: Direct registry registration ===")
    from tools.base_tool import registry

    # Register a simple hash tool
    code = """
import hashlib

def run_tool(input_data: dict) -> dict:
    \"\"\"Return the SHA-256 digest of the input text.\"\"\"
    text = input_data.get("text", "")
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {"success": True, "result": digest, "error": None}
"""
    ok = registry.register_dynamic(
        name="sha256_hasher",
        code=code,
        description="Compute SHA-256 digest of a text string.",
        input_schema={"text": "str — the text to hash"},
        output_schema={"result": "str — hex digest"},
        tags=["hashing", "crypto", "dynamic"],
        version="1.0.0",
    )
    print(f"  Registered: {ok}")

    # Call via unified interface
    result = await registry.call_tool("sha256_hasher", text="hello world")
    print(f"  call_tool result: {result}")

    # Verify persistence
    persist_path = Path("data/dynamic_tools/sha256_hasher.json")
    print(f"  Persisted to disk: {persist_path.exists()}")

    # List all tools (static + dynamic)
    all_tools = registry.describe_all_tools()
    dynamic_tools = [t for t in all_tools if t.get("dynamic")]
    print(f"  Total tools: {len(all_tools)}  Dynamic: {len(dynamic_tools)}")


# ---------------------------------------------------------------------------
# 2. Sandbox tester in isolation
# ---------------------------------------------------------------------------

async def demo_sandbox_tester() -> None:
    """Show sandbox_test_tool running code in an isolated subprocess."""
    print("\n=== Demo 2: Sandbox tester ===")
    from tools.sandbox_tester import sandbox_test_tool

    # Safe tool
    safe_code = """
def run_tool(input_data: dict) -> dict:
    n = input_data.get("n", 10)
    return {"success": True, "result": list(range(n)), "error": None}
"""
    passed, output = await sandbox_test_tool(safe_code, {"n": 5}, timeout=10)
    print(f"  Safe tool   → passed={passed}  output={output[:80]}")

    # Tool with a runtime error
    bad_code = """
def run_tool(input_data: dict) -> dict:
    raise ValueError("intentional error")
"""
    passed, output = await sandbox_test_tool(bad_code, {}, timeout=10)
    print(f"  Broken tool → passed={passed}  output={output[:80]}")

    # Timeout enforcement
    slow_code = """
import time
def run_tool(input_data: dict) -> dict:
    time.sleep(60)
    return {"success": True, "result": "done", "error": None}
"""
    passed, output = await sandbox_test_tool(slow_code, {}, timeout=3)
    print(f"  Slow tool   → passed={passed}  output={output[:60]}")


# ---------------------------------------------------------------------------
# 3. BaseAgent.call_tool() helper
# ---------------------------------------------------------------------------

async def demo_agent_call_tool() -> None:
    """Show agents calling tools via the BaseAgent.call_tool() helper."""
    print("\n=== Demo 3: BaseAgent.call_tool() helper ===")
    from agents.message_bus import MessageBus, bus
    from agents.coordinator_agent import CoordinatorAgent

    # Spin up just enough of the bus to test
    await bus.start()
    agent = CoordinatorAgent(bus)
    await agent.start()

    # Tool must be pre-registered (from Demo 1 above, or discovered tools)
    tools = agent.list_available_tools()
    print(f"  Agent sees {len(tools)} tool(s)")
    sample = tools[:3]
    for t in sample:
        print(f"    • {t['name']}: {t['description'][:60]}")

    exists = agent.tool_exists("sha256_hasher")
    print(f"  sha256_hasher registered: {exists}")

    if exists:
        result = await agent.call_tool("sha256_hasher", text="TASO rocks")
        print(f"  Result: {result}")

    await agent.stop()
    await bus.stop()


# ---------------------------------------------------------------------------
# 4. Full pipeline: detection → generation → registration → usage
#    (requires Ollama running; skips gracefully if unavailable)
# ---------------------------------------------------------------------------

async def demo_full_pipeline() -> None:
    """
    End-to-end demo using real LLM (Ollama deepseek-coder).
    PlannerAgent detects a missing tool, DeveloperAgent generates it,
    SecurityAgent tests it, MemoryAgent logs it.
    """
    print("\n=== Demo 4: Full pipeline (requires Ollama) ===")

    # Check Ollama availability
    import aiohttp
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get("http://localhost:11434/api/tags", timeout=aiohttp.ClientTimeout(total=3)) as r:
                if r.status != 200:
                    raise ConnectionError()
        print("  Ollama ✅ available")
    except Exception:
        print("  Ollama ❌ not available — skipping full pipeline demo")
        print("  Start Ollama with: ollama serve")
        return

    from agents.message_bus import bus
    from agents.developer_agent import DeveloperAgent
    from agents.security_agent import SecurityAnalysisAgent
    from agents.memory_agent import MemoryAgent
    from memory.knowledge_db import KnowledgeDB
    from memory.vector_store import VectorStore

    await bus.start()

    # Start supporting agents
    db      = KnowledgeDB()
    await db.connect()
    vector  = VectorStore()
    vector.load()

    mem_agent = MemoryAgent(bus, db, vector)
    await mem_agent.start()

    sec_agent = SecurityAnalysisAgent(bus)
    await sec_agent.start()

    dev_agent = DeveloperAgent(bus)
    await dev_agent.start()

    print("  Agents online: developer, security, memory")
    print("  Requesting tool: 'check if a TCP port is open'")

    # DeveloperAgent creates the tool and uses SecurityAgent + MemoryAgent via bus
    result = await dev_agent._generate_tool(
        "check if a given TCP port on a host is open or closed",
        tool_name_hint="tcp_port_check",
    )
    print(f"\n  Result:\n{result}")

    # Verify the tool is callable
    from tools.base_tool import registry
    if registry.tool_exists("tcp_port_check"):
        call_result = await registry.call_tool(
            "tcp_port_check", host="localhost", port=22
        )
        print(f"\n  Tool call result: {call_result}")
    else:
        print("\n  Tool not registered (generation may have failed — check Ollama logs)")

    await dev_agent.stop()
    await sec_agent.stop()
    await mem_agent.stop()
    await bus.stop()


# ---------------------------------------------------------------------------
# 5. Tool persistence reload
# ---------------------------------------------------------------------------

async def demo_persistence_reload() -> None:
    """Show that dynamic tools survive a registry restart."""
    print("\n=== Demo 5: Persistence reload ===")
    from pathlib import Path
    from tools.base_tool import ToolRegistry

    # Fresh registry (simulates restart)
    fresh_registry = ToolRegistry()
    fresh_registry.discover()

    persist_dir = Path("data/dynamic_tools")
    n = fresh_registry.load_persisted_tools(persist_dir)
    print(f"  Reloaded {n} persisted tool(s) into fresh registry")

    dynamic = fresh_registry.list_dynamic()
    for name in dynamic:
        print(f"    • {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    print("TASO Dynamic Tool Creation — Example Workflow")
    print("=" * 55)

    await demo_direct_registration()
    await demo_sandbox_tester()
    await demo_agent_call_tool()
    await demo_persistence_reload()
    await demo_full_pipeline()  # Requires Ollama — skips if unavailable

    print("\n✅ All demos complete.")


if __name__ == "__main__":
    asyncio.run(main())
