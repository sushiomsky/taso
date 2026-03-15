"""
TASO – tools/system_tools.py

Pre-built system-level tools available to all agents.

Included tools:
  • PortScannerTool    – check open TCP ports on a host
  • ProcessListerTool  – list running processes
  • NetworkCheckTool   – verify outbound connectivity
  • DiskUsageTool      – report disk utilisation by path
  • EnvInspectorTool   – list env vars (redacts secrets)
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import time
from typing import Any, Dict, List, Optional

import psutil

from tools.base_tool import BaseTool, ToolSchema


# ---------------------------------------------------------------------------
# PortScannerTool
# ---------------------------------------------------------------------------

class PortScannerTool(BaseTool):
    """Check which TCP ports are open on a target host."""

    name        = "port_scanner"
    description = "Scan a list of TCP ports on a target host and report which are open."
    schema      = ToolSchema({
            "host":    {"type": "str",  "required": True},
            "ports":   {"type": "list", "required": False, "default": [22, 80, 443, 8080, 8443]},
            "timeout": {"type": "float","required": False, "default": 1.0},
        }
    )

    async def execute(self, host: str, ports: List[int] = None, timeout: float = 1.0) -> Dict:
        if ports is None:
            ports = [22, 80, 443, 8080, 8443]

        open_ports:   List[int] = []
        closed_ports: List[int] = []

        async def _probe(port: int) -> None:
            try:
                fut = asyncio.open_connection(host, port)
                reader, writer = await asyncio.wait_for(fut, timeout=timeout)
                writer.close()
                await writer.wait_closed()
                open_ports.append(port)
            except Exception:
                closed_ports.append(port)

        await asyncio.gather(*[_probe(p) for p in ports])

        return {
            "host":         host,
            "open_ports":   sorted(open_ports),
            "closed_ports": sorted(closed_ports),
            "scanned":      len(ports),
        }


# ---------------------------------------------------------------------------
# ProcessListerTool
# ---------------------------------------------------------------------------

class ProcessListerTool(BaseTool):
    """List running processes, sorted by CPU or memory usage."""

    name        = "process_lister"
    description = "List running OS processes sorted by resource usage."
    schema      = ToolSchema({
            "sort_by": {"type": "str", "required": False, "default": "cpu_percent"},
            "limit":   {"type": "int", "required": False, "default": 20},
        }
    )

    async def execute(self, sort_by: str = "cpu_percent", limit: int = 20) -> Dict:
        loop = asyncio.get_event_loop()

        def _collect() -> List[Dict]:
            procs = []
            for p in psutil.process_iter(
                ["pid", "name", "cpu_percent", "memory_percent", "status", "username"]
            ):
                try:
                    info = p.info
                    info["cpu_percent"]    = p.cpu_percent(interval=0)
                    info["memory_percent"] = round(p.memory_percent(), 2)
                    procs.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            valid = [p for p in procs if sort_by in p]
            return sorted(valid, key=lambda x: x.get(sort_by, 0), reverse=True)[:limit]

        processes = await loop.run_in_executor(None, _collect)
        return {"processes": processes, "count": len(processes), "sort_by": sort_by}


# ---------------------------------------------------------------------------
# NetworkCheckTool
# ---------------------------------------------------------------------------

class NetworkCheckTool(BaseTool):
    """Verify outbound connectivity to a list of hosts."""

    name        = "network_check"
    description = "Check outbound TCP connectivity to specified hosts."
    schema      = ToolSchema({
            "hosts":   {"type": "list",  "required": False,
                        "default": ["8.8.8.8", "1.1.1.1", "github.com"]},
            "port":    {"type": "int",   "required": False, "default": 443},
            "timeout": {"type": "float", "required": False, "default": 3.0},
        }
    )

    async def execute(
        self,
        hosts: List[str] = None,
        port: int = 443,
        timeout: float = 3.0,
    ) -> Dict:
        if hosts is None:
            hosts = ["8.8.8.8", "1.1.1.1", "github.com"]

        results: Dict[str, Any] = {}

        async def _check(host: str) -> None:
            start = time.monotonic()
            try:
                fut = asyncio.open_connection(host, port)
                reader, writer = await asyncio.wait_for(fut, timeout=timeout)
                writer.close()
                await writer.wait_closed()
                latency_ms = round((time.monotonic() - start) * 1000, 1)
                results[host] = {"reachable": True, "latency_ms": latency_ms}
            except asyncio.TimeoutError:
                results[host] = {"reachable": False, "error": "timeout"}
            except Exception as exc:
                results[host] = {"reachable": False, "error": str(exc)[:60]}

        await asyncio.gather(*[_check(h) for h in hosts])
        reachable = sum(1 for v in results.values() if v["reachable"])
        return {
            "results":        results,
            "reachable_count": reachable,
            "total_checked":  len(hosts),
            "port":           port,
        }


# ---------------------------------------------------------------------------
# DiskUsageTool
# ---------------------------------------------------------------------------

class DiskUsageTool(BaseTool):
    """Report disk space utilisation for one or more paths."""

    name        = "disk_usage"
    description = "Report disk usage for specified filesystem paths."
    schema      = ToolSchema({
            "paths":  {"type": "list", "required": False, "default": ["/", "/tmp"]},
        }
    )

    async def execute(self, paths: List[str] = None) -> Dict:
        if paths is None:
            paths = ["/", "/tmp"]

        results: Dict[str, Any] = {}
        for path in paths:
            try:
                usage = psutil.disk_usage(path)
                results[path] = {
                    "total_gb": round(usage.total / 1e9, 2),
                    "used_gb":  round(usage.used  / 1e9, 2),
                    "free_gb":  round(usage.free  / 1e9, 2),
                    "percent":  usage.percent,
                }
            except Exception as exc:
                results[path] = {"error": str(exc)}

        return {"paths": results}


# ---------------------------------------------------------------------------
# EnvInspectorTool
# ---------------------------------------------------------------------------

_SECRET_PATTERN = re.compile(
    r"(key|token|secret|password|passwd|pwd|api|auth|credential|private)",
    re.I,
)


class EnvInspectorTool(BaseTool):
    """List environment variables, redacting likely secrets."""

    name        = "env_inspector"
    description = "List current environment variables with secrets redacted."
    schema      = ToolSchema({
            "prefix":   {"type": "str",  "required": False, "default": ""},
            "redact":   {"type": "bool", "required": False, "default": True},
        }
    )

    async def execute(self, prefix: str = "", redact: bool = True) -> Dict:
        env_vars: Dict[str, str] = {}
        for k, v in os.environ.items():
            if prefix and not k.startswith(prefix):
                continue
            if redact and _SECRET_PATTERN.search(k):
                env_vars[k] = "***REDACTED***"
            else:
                env_vars[k] = v[:200]  # truncate long values

        return {
            "variables":    env_vars,
            "count":        len(env_vars),
            "prefix_filter": prefix or "(none)",
        }
