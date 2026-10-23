"""EventBus: publish/subscribe per session id, mandatory keys, heartbeat."""

import asyncio

import pytest

from app.engine.events import HEARTBEAT_TYPE, EventBus, event, get_event_bus, reset_event_bus


async def test_publish_adds_ts_and_reaches_subscriber():
    bus = EventBus(heartbeat=5)
    gen = bus.subscribe("sid-a")
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)
    await bus.publish("sid-a", {"type": "chip", "round_n": 3, "member": "ravi", "status": "DECLINED"})
    ev = await asyncio.wait_for(task, 1)
    assert ev["type"] == "chip" and ev["member"] == "ravi"
    assert ev["ts"].endswith("Z")
    await gen.aclose()
    assert bus.subscriber_count("sid-a") == 0


async def test_events_are_isolated_per_session():
    bus = EventBus(heartbeat=0.05)
    gen = bus.subscribe("sid-b")
    await bus.publish("sid-a", {"type": "toast", "level": "info", "text": "wrong room"})
    first = await asyncio.wait_for(gen.__anext__(), 1)
    assert first["type"] == HEARTBEAT_TYPE
    await gen.aclose()
    assert bus.recent("sid-a")[-1]["text"] == "wrong room"
    assert bus.recent("sid-b") == []


async def test_publish_requires_a_type():
    bus = EventBus()
    with pytest.raises(ValueError):
        await bus.publish("sid", {"text": "no type"})


def test_event_helper_and_singleton():
    ev = event("board")
    assert ev["type"] == "board" and "ts" in ev
    bus = get_event_bus()
    assert get_event_bus() is bus
    assert reset_event_bus() is not bus
