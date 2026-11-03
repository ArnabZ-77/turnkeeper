"""GET /api/events -> text/event-stream of the caller's session events (sse-starlette).

The session comes from the HttpOnly tk_sid cookie only: the id is never in a URL, a log line or a response body.

Messages are unnamed (`data:` only) so `EventSource.onmessage` receives every event; the JSON carries
"type". Heartbeats are sent as SSE comments, invisible to the client.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.engine.common import iso, utcnow
from app.engine.events import HEARTBEAT_TYPE, get_event_bus
from app.engine.sessions import require_session
from app.models import Session

log = logging.getLogger("turnkeeper.routes.events")

router = APIRouter(prefix="/api", tags=["events"])


def format_sse(ev: dict[str, Any]) -> dict[str, Any]:
    """Map a bus event to the sse-starlette message dict."""
    if ev.get("type") == HEARTBEAT_TYPE:
        return {"comment": "heartbeat"}
    return {"data": json.dumps(ev, default=str, separators=(",", ":"))}


async def event_stream(sid: str, request: Request | None = None) -> AsyncIterator[dict[str, Any]]:
    yield format_sse({"type": "hello", "ts": iso(utcnow())})
    async for ev in get_event_bus().subscribe(sid):
        if request is not None and await request.is_disconnected():
            break
        yield format_sse(ev)


@router.get("/events")
async def session_events(request: Request, sess: Session = Depends(require_session)) -> EventSourceResponse:  # noqa: B008
    return EventSourceResponse(event_stream(sess.sid, request), ping=15)
