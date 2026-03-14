"""
TASO – Sandbox Tester for Dynamic Tools

Executes generated tool code in an isolated subprocess with resource limits.
Returns pass/fail with captured output.
"""
from __future__ import annotations
import asyncio
import json
import sys
import textwrap
from typing import Any, Dict, Optional, Tuple

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("sandbox_tester")

_HARNESS_TEMPLATE = '''
import json, sys, traceback

{tool_code}

try:
    result = run_tool({test_input_repr})
    print(json.dumps({{"success": True, "result": result}}))
except Exception as e:
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''


async def sandbox_test_tool(
    code: str,
    test_input: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Tuple[bool, str]:
    """
    Run `run_tool(test_input)` in an isolated subprocess.
    Returns (passed: bool, output: str).
    """
    test_input = test_input or {}
    harness = _HARNESS_TEMPLATE.format(
        tool_code=textwrap.indent(code, ""),
        test_input_repr=repr(test_input),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", harness,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,  # 1MB output limit
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return False, f"[Timeout after {timeout}s]"

        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        combined = f"{out}\n{err}".strip()

        if proc.returncode != 0:
            return False, f"[Exit {proc.returncode}]\n{combined}"

        # Try parsing JSON result
        try:
            data = json.loads(out)
            if data.get("success"):
                return True, json.dumps(data.get("result", {}), indent=2)
            else:
                return False, f"Tool error: {data.get('error', 'unknown')}\n{data.get('traceback','')}"
        except json.JSONDecodeError:
            # Non-JSON output still counts as pass if returncode == 0
            return True, out or "[no output]"

    except Exception as exc:
        return False, f"Sandbox error: {exc}"
