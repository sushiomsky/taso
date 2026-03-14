"""
TASO – Tool: system_monitor

Collects detailed host system metrics.
Wraps psutil to provide CPU, memory, disk, network, and process info.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

import psutil

from tools.base_tool import BaseTool, ToolSchema


class SystemMonitorTool(BaseTool):
    name        = "system_monitor"
    description = "Collect real-time CPU, memory, disk, network, and process metrics."
    schema      = ToolSchema({
        "include_processes": {
            "type": "bool", "required": False, "default": False,
            "description": "Include top-10 processes by CPU usage.",
        },
    })

    async def execute(self, include_processes: bool = False, **_: Any) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._collect, include_processes)

    # ------------------------------------------------------------------

    def _collect(self, include_processes: bool) -> Dict[str, Any]:
        # CPU
        cpu_freq  = psutil.cpu_freq()
        cpu_times = psutil.cpu_times_percent(interval=0.5)

        # Memory
        mem  = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # Disks
        disks: List[Dict] = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "device":     part.device,
                    "mountpoint": part.mountpoint,
                    "fstype":     part.fstype,
                    "total_gb":   round(usage.total / 1024**3, 2),
                    "used_gb":    round(usage.used  / 1024**3, 2),
                    "free_gb":    round(usage.free  / 1024**3, 2),
                    "percent":    usage.percent,
                })
            except Exception:
                pass

        # Network interfaces
        net_addrs  = psutil.net_if_addrs()
        net_stats  = psutil.net_if_stats()
        net_io     = psutil.net_io_counters(pernic=False)

        interfaces: List[Dict] = []
        for iface, addrs in net_addrs.items():
            stat = net_stats.get(iface)
            interfaces.append({
                "name":    iface,
                "up":      stat.isup if stat else False,
                "speed_mb": stat.speed if stat else 0,
                "addrs":   [
                    {"family": str(a.family), "address": a.address}
                    for a in addrs
                ],
            })

        # Processes
        procs: List[Dict] = []
        if include_processes:
            try:
                for p in sorted(
                    psutil.process_iter(["pid", "name", "cpu_percent",
                                         "memory_percent", "status"]),
                    key=lambda pr: pr.info["cpu_percent"] or 0.0,
                    reverse=True,
                )[:10]:
                    procs.append(p.info)
            except Exception:
                pass

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu": {
                "count_physical": psutil.cpu_count(logical=False),
                "count_logical":  psutil.cpu_count(logical=True),
                "percent":        psutil.cpu_percent(),
                "freq_mhz":       round(cpu_freq.current) if cpu_freq else None,
                "user_pct":       cpu_times.user,
                "system_pct":     cpu_times.system,
                "idle_pct":       cpu_times.idle,
            },
            "memory": {
                "total_mb":     round(mem.total   / 1024**2),
                "available_mb": round(mem.available / 1024**2),
                "used_mb":      round(mem.used    / 1024**2),
                "percent":      mem.percent,
                "swap_total_mb": round(swap.total / 1024**2),
                "swap_used_mb":  round(swap.used  / 1024**2),
                "swap_percent":  swap.percent,
            },
            "disks":      disks,
            "network": {
                "interfaces":    interfaces,
                "bytes_sent_mb": round(net_io.bytes_sent  / 1024**2, 1),
                "bytes_recv_mb": round(net_io.bytes_recv  / 1024**2, 1),
                "packets_sent":  net_io.packets_sent,
                "packets_recv":  net_io.packets_recv,
                "errors_in":     net_io.errin,
                "errors_out":    net_io.errout,
            },
            "processes": procs,
            "boot_time": datetime.fromtimestamp(
                psutil.boot_time(), tz=timezone.utc
            ).isoformat(),
        }
