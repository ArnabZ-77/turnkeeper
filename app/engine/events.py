"""Process-wide SSE event bus: per-session asyncio queues with a heartbeat.

Event types and payloads are fixed by docs/CONTRACTS.md (chip, trace, payout, ticker, toast, queue,
board, schedule_changed). Every published event carries "type" and "ts".
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.engine.common import iso, utcnow

log = logging.getLogger("turnkeeper.engine.events")

Publish = Callable[[dict], Awaitable[None]]

HEARTBEAT_TYPE = "heartbeat"


class EventBus:
    """Fan-out of events to every subscriber of a session id.

    `subscribe()` is an async generator; it yields a heartbeat event every `heartbeat` seconds when
    nothing else arrives so SSE connections stay alive behind proxies.
    """

    def __init__(self, heartbeat: float = 15.0, history: int = 200, maxsize: int = 1000) -> None:
        self.heartbeat = heartbeat
        self._maxsize = maxsize
        self._subs: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)
        self._history: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=history))

    async def publish(self, sid: str, event: dict) -> None:
        if "type" not in event:
            raise ValueError("event needs a 'type'")
        ev: dict[str, Any] = dict(event)
        ev.setdefault("ts", iso(utcnow()))
        self._history[sid].append(ev)
        for q in list(self._subs.get(sid, ())):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # A stalled subscriber must not block the engine: drop its oldest event.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait(ev)

    async def subscribe(self, sid: str) -> AsyncIterator[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._maxsize)
        self._subs[sid].add(q)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=self.heartbeat)
                except TimeoutError:
                    ev = {"type": HEARTBEAT_TYPE, "ts": iso(utcnow())}
                yield ev
        finally:
            self._subs[sid].discard(q)
            if not self._subs[sid]:
                self._subs.pop(sid, None)

    def publisher(self, sid: str) -> Publish:
        async def _publish(event: dict) -> None:
            await self.publish(sid, event)

        return _publish

    def recent(self, sid: str, limit: int = 50) -> list[dict]:
        return list(self._history.get(sid, ()))[-limit:]

    def subscriber_count(self, sid: str) -> int:
        return len(self._subs.get(sid, ()))

    def clear(self) -> None:
        self._subs.clear()
        self._history.clear()


_BUS: EventBus | None = None


def get_event_bus() -> EventBus:
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS


def reset_event_bus() -> EventBus:
    """Replace the singleton (tests)."""
    global _BUS
    _BUS = EventBus()
    return _BUS


def event(type_: str, **payload: Any) -> dict:
    """Build an event dict with the mandatory keys."""
    ev: dict[str, Any] = {"type": type_, "ts": iso(utcnow())}
    ev.update(payload)
    return ev
