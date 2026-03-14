"""
TASO – Agent message bus.

All inter-agent communication flows through this async pub/sub bus.
Each agent subscribes to one or more "topics".  The orchestrator
publishes tasks and agents reply with results.

Message schema (dataclass):
  BusMessage
    id          – unique UUID
    topic       – routing key  (e.g. "security.scan", "research.cve")
    sender      – agent name
    recipient   – target agent name or "*" for broadcast
    payload     – arbitrary dict
    reply_to    – optional topic for the response
    ts          – ISO timestamp
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("agent")

MessageHandler = Callable[["BusMessage"], Coroutine[Any, Any, None]]


@dataclass
class BusMessage:
    topic: str
    sender: str
    payload: Dict[str, Any] = field(default_factory=dict)
    recipient: str = "*"
    reply_to: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def reply(self, payload: Dict[str, Any], sender: str) -> "BusMessage":
        """Create a reply message on the reply_to topic."""
        if not self.reply_to:
            raise ValueError("Original message has no reply_to topic.")
        return BusMessage(
            topic=self.reply_to,
            sender=sender,
            recipient=self.sender,
            payload=payload,
            id=str(uuid.uuid4()),
        )


class MessageBus:
    """
    Simple async publish/subscribe message bus.

    Agents call `subscribe(topic_prefix, handler)` during startup.
    The bus matches incoming messages whose topic starts with the
    subscribed prefix (exact match is also accepted).
    """

    def __init__(self) -> None:
        self._subscriptions: Dict[str, List[MessageHandler]] = {}
        self._queue: asyncio.Queue = asyncio.Queue(
            maxsize=settings.BUS_MAX_QUEUE
        )
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, topic_prefix: str, handler: MessageHandler) -> None:
        self._subscriptions.setdefault(topic_prefix, []).append(handler)
        log.debug(f"Bus: subscribed handler to '{topic_prefix}'")

    def unsubscribe(self, topic_prefix: str, handler: MessageHandler) -> None:
        if topic_prefix in self._subscriptions:
            self._subscriptions[topic_prefix].remove(handler)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, message: BusMessage) -> None:
        await self._queue.put(message)
        log.debug(f"Bus: published [{message.topic}] from {message.sender}")

    async def publish_and_wait(
        self, message: BusMessage, timeout: float = 30.0
    ) -> Optional[BusMessage]:
        """Publish a message and wait for a response on message.reply_to."""
        if not message.reply_to:
            raise ValueError("publish_and_wait requires reply_to to be set.")

        result_queue: asyncio.Queue = asyncio.Queue(maxsize=1)

        async def _capture(msg: BusMessage) -> None:
            await result_queue.put(msg)

        self.subscribe(message.reply_to, _capture)
        try:
            await self.publish(message)
            return await asyncio.wait_for(result_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(f"publish_and_wait timed out for topic={message.topic}")
            return None
        finally:
            self.unsubscribe(message.reply_to, _capture)

    # ------------------------------------------------------------------
    # Dispatch loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop())
        log.info("MessageBus started.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("MessageBus stopped.")

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                msg: BusMessage = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            handlers = self._find_handlers(msg.topic)
            if not handlers:
                log.debug(f"Bus: no handlers for topic '{msg.topic}'")
                continue

            for handler in handlers:
                asyncio.create_task(self._safe_call(handler, msg))

    def _find_handlers(self, topic: str) -> List[MessageHandler]:
        """Find all handlers whose prefix matches the topic."""
        matches: List[MessageHandler] = []
        for prefix, handlers in self._subscriptions.items():
            if topic == prefix or topic.startswith(prefix + "."):
                matches.extend(handlers)
        return matches

    @staticmethod
    async def _safe_call(handler: MessageHandler, msg: BusMessage) -> None:
        try:
            await handler(msg)
        except Exception as exc:
            log.error(f"Bus handler error for topic '{msg.topic}': {exc}")


# ---------------------------------------------------------------------------
# Singleton bus instance
# ---------------------------------------------------------------------------
bus = MessageBus()
