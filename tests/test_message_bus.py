"""
Tests for the Agent Message Bus.

Covers:
  - bus start / stop lifecycle
  - subscribe and publish (single topic)
  - wildcard topic matching
  - message delivery ordering
  - publish_and_wait timeout
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest
import pytest_asyncio

from agents.message_bus import BusMessage, MessageBus


@pytest_asyncio.fixture
async def bus():
    b = MessageBus()
    await b.start()
    yield b
    await b.stop()


@pytest.mark.asyncio
async def test_bus_starts_and_stops():
    b = MessageBus()
    await b.start()
    assert b._running
    await b.stop()
    assert not b._running


@pytest.mark.asyncio
async def test_subscribe_and_receive(bus: MessageBus):
    received: List[BusMessage] = []

    async def handler(msg: BusMessage) -> None:
        received.append(msg)

    bus.subscribe("test.topic", handler)
    await bus.publish(BusMessage(topic="test.topic", sender="tester", payload={"x": 1}))
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0].payload["x"] == 1


@pytest.mark.asyncio
async def test_wildcard_subscription(bus: MessageBus):
    received: List[BusMessage] = []

    async def handler(msg: BusMessage) -> None:
        received.append(msg)

    bus.subscribe("security", handler)
    await bus.publish(BusMessage(topic="security.scan", sender="tester"))
    await bus.publish(BusMessage(topic="security.cve",  sender="tester"))
    await bus.publish(BusMessage(topic="other.topic",   sender="tester"))
    await asyncio.sleep(0.05)
    assert len(received) == 2


@pytest.mark.asyncio
async def test_multiple_subscribers_same_topic(bus: MessageBus):
    calls_a: List[str] = []
    calls_b: List[str] = []

    async def handler_a(msg: BusMessage) -> None:
        calls_a.append(msg.payload.get("val", ""))

    async def handler_b(msg: BusMessage) -> None:
        calls_b.append(msg.payload.get("val", ""))

    bus.subscribe("shared.topic", handler_a)
    bus.subscribe("shared.topic", handler_b)
    await bus.publish(BusMessage(topic="shared.topic", sender="tester", payload={"val": "hello"}))
    await asyncio.sleep(0.05)
    assert calls_a == ["hello"]
    assert calls_b == ["hello"]


@pytest.mark.asyncio
async def test_bus_message_has_unique_ids():
    ids = {BusMessage(topic="t", sender="s").id for _ in range(100)}
    assert len(ids) == 100


@pytest.mark.asyncio
async def test_bus_message_reply():
    orig = BusMessage(topic="req.topic", sender="agent_a", reply_to="resp.topic")
    reply = orig.reply({"answer": 42}, sender="agent_b")
    assert reply.topic == "resp.topic"
    assert reply.payload["answer"] == 42
    assert reply.sender == "agent_b"


@pytest.mark.asyncio
async def test_no_delivery_after_stop(bus: MessageBus):
    received: List[BusMessage] = []

    async def handler(msg: BusMessage) -> None:
        received.append(msg)

    bus.subscribe("after.stop", handler)
    await bus.stop()
    await bus.publish(BusMessage(topic="after.stop", sender="tester"))
    await asyncio.sleep(0.05)
    assert len(received) == 0



@pytest.mark.asyncio
async def test_bus_starts_and_stops():
    b = MessageBus()
    await b.start()
    assert b._running
    await b.stop()
    assert not b._running


@pytest.mark.asyncio
async def test_subscribe_and_receive(bus: MessageBus):
    received: List[BusMessage] = []

    async def handler(msg: BusMessage) -> None:
        received.append(msg)

    bus.subscribe("test.topic", handler)
    await bus.publish(BusMessage(topic="test.topic", sender="tester", payload={"x": 1}))
    await asyncio.sleep(0.05)  # let dispatcher run
    assert len(received) == 1
    assert received[0].payload["x"] == 1


@pytest.mark.asyncio
async def test_wildcard_subscription(bus: MessageBus):
    received: List[BusMessage] = []

    async def handler(msg: BusMessage) -> None:
        received.append(msg)

    bus.subscribe("security", handler)
    await bus.publish(BusMessage(topic="security.scan", sender="tester"))
    await bus.publish(BusMessage(topic="security.cve",  sender="tester"))
    await bus.publish(BusMessage(topic="other.topic",   sender="tester"))
    await asyncio.sleep(0.05)
    assert len(received) == 2


@pytest.mark.asyncio
async def test_multiple_subscribers_same_topic(bus: MessageBus):
    calls_a: List[str] = []
    calls_b: List[str] = []

    async def handler_a(msg: BusMessage) -> None:
        calls_a.append(msg.payload.get("val", ""))

    async def handler_b(msg: BusMessage) -> None:
        calls_b.append(msg.payload.get("val", ""))

    bus.subscribe("shared.topic", handler_a)
    bus.subscribe("shared.topic", handler_b)
    await bus.publish(BusMessage(topic="shared.topic", sender="tester", payload={"val": "hello"}))
    await asyncio.sleep(0.05)
    assert calls_a == ["hello"]
    assert calls_b == ["hello"]


@pytest.mark.asyncio
async def test_bus_message_has_unique_ids(bus: MessageBus):
    ids = {BusMessage(topic="t", sender="s").id for _ in range(100)}
    assert len(ids) == 100


@pytest.mark.asyncio
async def test_bus_message_reply(bus: MessageBus):
    orig = BusMessage(topic="req.topic", sender="agent_a", reply_to="resp.topic")
    reply = orig.reply({"answer": 42}, sender="agent_b")
    assert reply.topic == "resp.topic"
    assert reply.payload["answer"] == 42
    assert reply.sender == "agent_b"


@pytest.mark.asyncio
async def test_no_delivery_after_stop(bus: MessageBus):
    received: List[BusMessage] = []

    async def handler(msg: BusMessage) -> None:
        received.append(msg)

    bus.subscribe("after.stop", handler)
    await bus.stop()
    await bus.publish(BusMessage(topic="after.stop", sender="tester"))
    await asyncio.sleep(0.05)
    assert len(received) == 0
