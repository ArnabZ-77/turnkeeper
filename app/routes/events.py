"""GET /api/sessions/{sid}/events -> text/event-stream of EventBus events (sse-starlette).

Messages are unnamed (`data:` only) so `EventSource.onmessage` receives every event; the JSON carries
"type". Heartbeats are sent as SSE comments, invisible to the client.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.engine import sessions
from app.engine.common import iso, utcnow
from app.engine.events import HEARTBEAT_TYPE, get_event_bus

log = logging.getLogger("turnkeeper.routes.events")

router = APIRouter(prefix="/api", tags=["events"])


def format_sse(ev: dict[str, Any]) -> dict[str, Any]:
    """Map a bus event to the sse-starlette message dict."""
    if ev.get("type") == HEARTBEAT_TYPE:
        return {"comment": "heartbeat"}
    return {"data": json.dumps(ev, default=str, separators=(",", ":"))}


async def event_stream(sid: str, request: Request | None = None) -> AsyncIterator[dict[str, Any]]:
    yield format_sse({"type": "hello", "ts": iso(utcnow()), "sid": sid})
    async for ev in get_event_bus().subscribe(sid):
        if request is not None and await request.is_disconnected():
            break
        yield format_sse(ev)


@router.get("/sessions/{sid}/events")
async def session_events(sid: str, request: Request) -> EventSourceResponse:
    if sessions.resolve_session(sid) is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return EventSourceResponse(event_stream(sid, request), ping=15)
