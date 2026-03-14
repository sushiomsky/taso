"""
TASO – SystemAgent

Monitors host resources, reports system health, and manages
the TASO process itself.

Bus topics consumed:
  system.status   – return current system metrics
  system.logs     – return recent log lines

Bus topics published:
  coordinator.result.<task_id>
"""

from __future__ import annotations

import asyncio
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import psutil

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from config.settings import settings

log = get_logger("agent")


class SystemAgent(BaseAgent):
    name = "system"
    description = "Host resource monitoring and system health reporting."

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("system.status", self._handle_status)
        self._bus.subscribe("system.logs",   self._handle_logs)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_status(self, msg: BusMessage) -> None:
        task_id = msg.payload.get("task_id", "")
        metrics = await self._collect_metrics()
        result  = {"task_id": task_id, "metrics": metrics}
        await self._reply(msg, result)

    async def _handle_logs(self, msg: BusMessage) -> None:
        task_id  = msg.payload.get("task_id", "")
        category = msg.payload.get("category", "combined")
        lines    = int(msg.payload.get("lines", 50))

        log_lines = self._read_log(category, lines)
        result    = {
            "task_id":  task_id,
            "category": category,
            "lines":    log_lines,
        }
        await self._reply(msg, result)

    # ------------------------------------------------------------------
    # Metrics collection
    # ------------------------------------------------------------------

    async def _collect_metrics(self) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._collect_metrics_sync)

    @staticmethod
    def _collect_metrics_sync() -> Dict[str, Any]:
        cpu_pct  = psutil.cpu_percent(interval=0.5)
        mem      = psutil.virtual_memory()
        disk     = psutil.disk_usage("/")
        net_io   = psutil.net_io_counters()
        boot_ts  = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat()

        # Docker availability check
        try:
            import docker
            client  = docker.from_env()
            containers = len(client.containers.list())
            docker_ok = True
        except Exception:
            containers = 0
            docker_ok  = False

        return {
            "hostname":        platform.node(),
            "os":              platform.system(),
            "python":          platform.python_version(),
            "boot_time":       boot_ts,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "cpu": {
                "percent":     cpu_pct,
                "cores":       psutil.cpu_count(logical=False),
                "logical":     psutil.cpu_count(logical=True),
            },
            "memory": {
                "total_mb":    round(mem.total / 1024 / 1024),
                "used_mb":     round(mem.used  / 1024 / 1024),
                "percent":     mem.percent,
            },
            "disk": {
                "total_gb":    round(disk.total / 1024**3, 1),
                "used_gb":     round(disk.used  / 1024**3, 1),
                "percent":     disk.percent,
            },
            "network": {
                "bytes_sent_mb":  round(net_io.bytes_sent  / 1024 / 1024, 1),
                "bytes_recv_mb":  round(net_io.bytes_recv  / 1024 / 1024, 1),
            },
            "docker": {
                "available":   docker_ok,
                "containers":  containers,
            },
        }

    # ------------------------------------------------------------------
    # Log reader
    # ------------------------------------------------------------------

    @staticmethod
    def _read_log(category: str, lines: int) -> List[str]:
        log_file = settings.LOG_DIR / f"{category}.log"
        if not log_file.exists():
            return [f"No log file found for category '{category}'."]
        try:
            text  = log_file.read_text(errors="ignore")
            all_lines = text.splitlines()
            return all_lines[-lines:]
        except Exception as exc:
            return [f"Error reading log: {exc}"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _reply(self, msg: BusMessage, result: Dict) -> None:
        if msg.reply_to:
            await self._bus.publish(
                BusMessage(
                    topic=msg.reply_to,
                    sender=self.name,
                    recipient=msg.sender,
                    payload=result,
                )
            )

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use — returns system metrics summary."""
        metrics = await self._collect_metrics()
        import json as _json
        return _json.dumps(metrics, indent=2, default=str)
