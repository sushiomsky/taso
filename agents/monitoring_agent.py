"""
TASO – MonitoringAgent

Tracks system health, log error rates, and agent liveness.
Runs periodic internal health-checks and publishes heartbeat events.

Bus topics consumed:
  monitoring.status       – return current health snapshot
  monitoring.alert        – register a manual alert
  monitoring.errors       – return recent error summary

Bus topics published:
  coordinator.heartbeat   – periodic heartbeat payload
  memory.store            – persist health snapshots
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import psutil

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage
from config.logging_config import get_logger
from config.settings import settings

log = get_logger("agent")

_HEARTBEAT_INTERVAL = 60   # seconds between heartbeat publishes
_SNAPSHOT_RETAIN    = 120  # number of snapshots to keep in memory


class MonitoringAgent(BaseAgent):
    """
    Monitors host resources, log error rates, and agent health.

    Continuously samples CPU / memory / disk and maintains a rolling
    window of snapshots that any agent can query.
    """

    name        = "monitoring"
    description = "System health monitoring, log error tracking, and alerting."

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, bus: Any) -> None:
        super().__init__(bus)
        self._snapshots: Deque[Dict] = deque(maxlen=_SNAPSHOT_RETAIN)
        self._alerts:    List[Dict]  = []
        self._start_time: float      = time.time()
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await super().start()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="monitoring_heartbeat"
        )

    async def stop(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await super().stop()

    # ------------------------------------------------------------------
    # Bus subscriptions
    # ------------------------------------------------------------------

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("monitoring.status",  self._handle_status)
        self._bus.subscribe("monitoring.alert",   self._handle_alert)
        self._bus.subscribe("monitoring.errors",  self._handle_errors)
        self._bus.subscribe("monitoring.metrics", self._handle_metrics)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_status(self, msg: BusMessage) -> None:
        snapshot = await self._take_snapshot()
        result   = {
            "snapshot":    snapshot,
            "uptime_s":    int(time.time() - self._start_time),
            "alerts":      self._alerts[-10:],
            "recent_logs": self._tail_logs(50),
        }
        await self._reply(msg, result)

    async def _handle_alert(self, msg: BusMessage) -> None:
        alert = {
            "ts":      time.time(),
            "source":  msg.sender,
            "message": msg.payload.get("message", ""),
            "level":   msg.payload.get("level", "info"),
        }
        self._alerts.append(alert)
        log.warning(f"MonitoringAgent alert from {msg.sender}: {alert['message']}")
        await self._reply(msg, {"status": "recorded", "alert": alert})

    async def _handle_errors(self, msg: BusMessage) -> None:
        lines = msg.payload.get("lines", 200)
        errors, warnings = self._parse_log_errors(lines)
        result = {
            "total_errors":   len(errors),
            "total_warnings": len(warnings),
            "recent_errors":  errors[-5:],
            "recent_warnings":warnings[-5:],
        }
        await self._reply(msg, result)

    async def _handle_metrics(self, msg: BusMessage) -> None:
        snapshot = await self._take_snapshot()
        await self._reply(msg, {"metrics": snapshot})

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Take a snapshot and publish coordinator.heartbeat every minute."""
        while True:
            try:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                snapshot = await self._take_snapshot()
                self._snapshots.append(snapshot)

                await self._bus.publish(
                    BusMessage(
                        topic="coordinator.heartbeat",
                        sender=self.name,
                        payload={"snapshot": snapshot},
                    )
                )

                # Persist to memory periodically
                await self._bus.publish(
                    BusMessage(
                        topic="memory.store",
                        sender=self.name,
                        payload={
                            "category": "system_health",
                            "text":     self._snapshot_summary(snapshot),
                            "metadata": snapshot,
                        },
                    )
                )

                # Auto-alert on high resource usage
                self._check_thresholds(snapshot)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(f"MonitoringAgent heartbeat error: {exc}")

    # ------------------------------------------------------------------
    # Resource collection
    # ------------------------------------------------------------------

    async def _take_snapshot(self) -> Dict[str, Any]:
        """Collect current system metrics (non-blocking via executor)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._collect_sync)

    @staticmethod
    def _collect_sync() -> Dict[str, Any]:
        try:
            cpu  = psutil.cpu_percent(interval=0.5)
            mem  = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net  = psutil.net_io_counters()
            return {
                "ts":               time.time(),
                "cpu_pct":          cpu,
                "mem_used_gb":      round(mem.used  / 1e9, 2),
                "mem_total_gb":     round(mem.total / 1e9, 2),
                "mem_pct":          mem.percent,
                "disk_used_gb":     round(disk.used  / 1e9, 2),
                "disk_total_gb":    round(disk.total / 1e9, 2),
                "disk_pct":         disk.percent,
                "net_bytes_sent":   net.bytes_sent,
                "net_bytes_recv":   net.bytes_recv,
                "load_avg":         list(psutil.getloadavg()),
            }
        except Exception as exc:
            return {"error": str(exc), "ts": time.time()}

    def _snapshot_summary(self, snap: Dict) -> str:
        return (
            f"CPU {snap.get('cpu_pct', '?')}% | "
            f"RAM {snap.get('mem_pct', '?')}% | "
            f"Disk {snap.get('disk_pct', '?')}%"
        )

    def _check_thresholds(self, snap: Dict) -> None:
        """Emit an internal alert if resource usage is critical."""
        thresholds = [
            ("cpu_pct",  90, "CPU usage critical"),
            ("mem_pct",  90, "Memory usage critical"),
            ("disk_pct", 95, "Disk usage critical"),
        ]
        for key, limit, msg in thresholds:
            val = snap.get(key, 0)
            if isinstance(val, (int, float)) and val >= limit:
                self._alerts.append({
                    "ts":      snap["ts"],
                    "source":  "monitoring_agent",
                    "message": f"{msg}: {val}%",
                    "level":   "critical",
                })
                log.critical(f"MonitoringAgent: {msg}: {val}%")

    # ------------------------------------------------------------------
    # Log parsing helpers
    # ------------------------------------------------------------------

    def _tail_logs(self, lines: int = 50) -> List[str]:
        log_file = Path(settings.BASE_DIR) / "logs" / "agent.log"
        try:
            text = log_file.read_text(errors="ignore")
            return text.splitlines()[-lines:]
        except Exception:
            return []

    def _parse_log_errors(
        self, lines: int = 200
    ) -> Tuple[List[str], List[str]]:
        """Return (errors, warnings) from the tail of the log file."""
        tail   = self._tail_logs(lines)
        errors   = [l for l in tail if "| ERROR"   in l or "| CRITICAL" in l]
        warnings = [l for l in tail if "| WARNING" in l]
        return errors, warnings

    # ------------------------------------------------------------------
    # Swarm-callable interface
    # ------------------------------------------------------------------

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable: return a formatted health snapshot."""
        snap = await self._take_snapshot()
        err, warn = self._parse_log_errors(100)
        return (
            f"🖥️  System Health\n"
            f"CPU: {snap.get('cpu_pct','?')}%  |  "
            f"RAM: {snap.get('mem_pct','?')}%  |  "
            f"Disk: {snap.get('disk_pct','?')}%\n"
            f"Load: {snap.get('load_avg', ['?','?','?'])}\n"
            f"Errors (last 100 log lines): {len(err)}  |  "
            f"Warnings: {len(warn)}\n"
            f"Alerts: {len(self._alerts)}"
        )

    # ------------------------------------------------------------------
    # Internal reply helper
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
