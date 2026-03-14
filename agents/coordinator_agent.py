"""
TASO – CoordinatorAgent

The central orchestrator of the multi-agent system.

Responsibilities:
  • Receive top-level task requests from the Telegram bot
  • Break them down into sub-tasks
  • Route sub-tasks to the correct specialist agents via the bus
  • Aggregate results and return a summary
  • Maintain a task registry so Telegram can poll status

Bus topics consumed:
  coordinator.task          – new task from bot or another agent
  coordinator.status        – status request

Bus topics published:
  security.*
  research.*
  dev.*
  memory.*
  system.*
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger

log = get_logger("agent")

# Task status literals
PENDING = "pending"
RUNNING = "running"
DONE    = "done"
FAILED  = "failed"


class CoordinatorAgent(BaseAgent):
    name = "coordinator"
    description = "Manages workflows, distributes tasks, aggregates results."

    def __init__(self, bus: MessageBus) -> None:
        super().__init__(bus)
        # task_id -> task record
        self._tasks: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("coordinator.task", self._handle_task)
        self._bus.subscribe("coordinator.status", self._handle_status_request)
        self._bus.subscribe("coordinator.result", self._handle_result)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_task(self, msg: BusMessage) -> None:
        """
        Incoming task payload:
          {
            "command": "scan_repo" | "threat_intel" | "code_audit" | ...,
            "args": {...},
            "reply_to_chat": <int chat_id – optional>
          }
        """
        try:
            command = msg.payload.get("command", "unknown")
            args = msg.payload.get("args", {})
            task_id = str(uuid.uuid4())

            record: Dict[str, Any] = {
                "id": task_id,
                "command": command,
                "args": args,
                "status": RUNNING,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "results": [],
                "reply_to_chat": msg.payload.get("reply_to_chat"),
                "reply_topic": msg.reply_to,
            }
            self._tasks[task_id] = record

            log.info(f"Coordinator received task: {command} (id={task_id})")

            # Route to appropriate agent(s)
            asyncio.create_task(self._route_task(task_id, command, args, msg))
        except Exception as exc:
            log.error(f"Error handling task message: {exc}")
            if msg.reply_to:
                await self._bus.publish(
                    BusMessage(
                        topic=msg.reply_to,
                        sender=self.name,
                        recipient=msg.sender,
                        payload={"error": f"Failed to process task: {str(exc)}"},
                    )
                )

    async def _handle_status_request(self, msg: BusMessage) -> None:
        try:
            task_id = msg.payload.get("task_id")
            if task_id:
                record = self._tasks.get(task_id)
                payload = record if record else {"error": "Task not found"}
            else:
                # Return summary of recent tasks
                recent = sorted(
                    self._tasks.values(),
                    key=lambda t: t["created_at"],
                    reverse=True,
                )[:10]
                payload = {"tasks": recent}

            if msg.reply_to:
                await self._bus.publish(
                    BusMessage(
                        topic=msg.reply_to,
                        sender=self.name,
                        recipient=msg.sender,
                        payload=payload,
                    )
                )
        except Exception as exc:
            log.error(f"Error handling status request: {exc}")
            if msg.reply_to:
                await self._bus.publish(
                    BusMessage(
                        topic=msg.reply_to,
                        sender=self.name,
                        recipient=msg.sender,
                        payload={"error": f"Failed to process status request: {str(exc)}"},
                    )
                )

    async def _handle_result(self, msg: BusMessage) -> None:
        """Collect sub-task results back from agents."""
        try:
            task_id = msg.payload.get("task_id")
            if not task_id or task_id not in self._tasks:
                log.warning(f"Received result for unknown task_id: {task_id}")
                return

            record = self._tasks[task_id]
            record["results"].append({
                "from": msg.sender,
                "payload": msg.payload,
            })
        except Exception as exc:
            log.error(f"Error handling result message: {exc}")

    # ------------------------------------------------------------------
    # Routing logic
    # ------------------------------------------------------------------

    async def _route_task(self, task_id: str, command: str,
                          args: Dict, original_msg: BusMessage) -> None:
        try:
            result = await self._dispatch(task_id, command, args)
            self._tasks[task_id]["status"] = DONE
            self._tasks[task_id]["result"] = result
        except Exception as exc:
            log.error(f"Task {task_id} failed: {exc}")
            self._tasks[task_id]["status"] = FAILED
            self._tasks[task_id]["error"] = str(exc)
            result = {"error": str(exc)}

        # Notify caller if reply_to is set
        if original_msg.reply_to:
            try:
                await self._bus.publish(
                    BusMessage(
                        topic=original_msg.reply_to,
                        sender=self.name,
                        recipient=original_msg.sender,
                        payload={"task_id": task_id, "result": result},
                    )
                )
            except Exception as exc:
                log.error(f"Failed to send reply for task {task_id}: {exc}")

    async def _dispatch(self, task_id: str, command: str,
                        args: Dict) -> Dict[str, Any]:
        """
        Map command → target agent topic and wait for result.
        """
        reply_topic = f"coordinator.result.{task_id}"
        result_queue: asyncio.Queue = asyncio.Queue(1)

        async def _capture(msg: BusMessage) -> None:
            await result_queue.put(msg)

        self._bus.subscribe(reply_topic, _capture)

        try:
            target_topic, payload = self._build_sub_task(
                task_id, command, args, reply_topic
            )
            await self._bus.publish(
                BusMessage(
                    topic=target_topic,
                    sender=self.name,
                    payload=payload,
                    reply_to=reply_topic,
                )
            )
            response: BusMessage = await asyncio.wait_for(
                result_queue.get(), timeout=120.0
            )
            return response.payload
        except asyncio.TimeoutError:
            log.error(f"Timeout waiting for response to task {task_id} ({command})")
            return {"error": f"Timeout waiting for response to '{command}'"}
        except Exception as exc:
            log.error(f"Error dispatching task {task_id}: {exc}")
            return {"error": f"Failed to dispatch task: {str(exc)}"}
        finally:
            self._bus.unsubscribe(reply_topic, _capture)

    @staticmethod
    def _build_sub_task(
        task_id: str, command: str, args: Dict, reply_topic: str
    ) -> tuple[str, Dict]:
        """Return (bus_topic, payload) for a given high-level command."""
        mapping = {
            "scan_repo": ("security.scan_repo", args),
            "security_scan": ("security.full_scan", args),
            "code_audit": ("security.code_audit", args),
            "threat_intel": ("research.threat_intel", args),
            "update_self": ("dev.update_self", args),
            "system_status": ("system.status", args),
            "memory_query": ("memory.query", args),
        }
        topic, payload = mapping.get(command, ("system.status", args))
        return topic, {**payload, "task_id": task_id}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["total_tasks"] = len(self._tasks)
        base["running_tasks"] = sum(1 for t in self._tasks.values() if t["status"] == RUNNING)
        base["done_tasks"] = sum(1 for t in self._tasks.values() if t["status"] == DONE)
        return base

    def get_task(self, task_id: str) -> Optional[Dict]:
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 10) -> List[Dict]:
        return sorted(
            self._tasks.values(),
            key=lambda t: t["created_at"],
            reverse=True,
        )[:limit]

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use — routes via LLM."""
        prompt = description
        if context:
            prompt = f"{context}\n\nTask: {description}"
        return await self.llm_query(prompt)
