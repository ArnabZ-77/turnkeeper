"""GET /api/sessions/{sid}/events: text/event-stream carrying EventBus events.

Starlette's TestClient buffers a response until the ASGI app returns, so an endless SSE stream cannot be
read through it. The streaming test drives the ASGI app directly on the test loop with a bounded
timeout: open the stream, publish on the bus, read until the event shows up, then send http.disconnect.
"""

import asyncio
import json

from app.engine.events import get_event_bus
from app.engine.sessions import create_session
from app.routes.events import format_sse


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/nope/events").status_code == 404


def test_format_sse_maps_heartbeats_to_comments():
    assert format_sse({"type": "heartbeat", "ts": "x"}) == {"comment": "heartbeat"}
    out = format_sse({"type": "toast", "level": "info", "text": "hi", "ts": "x"})
    assert json.loads(out["data"])["type"] == "toast"


async def test_stream_yields_a_published_event(app):
    sess = create_session(enforce_cap=False)
    sid = sess.sid
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/api/sessions/{sid}/events",
        "raw_path": f"/api/sessions/{sid}/events".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    disconnect = asyncio.Event()
    got_body = asyncio.Event()
    chunks: list[bytes] = []
    headers: dict[str, str] = {}

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            headers.update({k.decode(): v.decode() for k, v in message["headers"]})
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if b"toast" in b"".join(chunks):
                got_body.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        # wait for the hello frame, then publish something for this session only
        for _ in range(100):
            if chunks:
                break
            await asyncio.sleep(0.01)
        await get_event_bus().publish("someone-else", {"type": "toast", "level": "info", "text": "not yours"})
        await get_event_bus().publish(sid, {"type": "toast", "level": "success", "text": "sse works"})
        await asyncio.wait_for(got_body.wait(), timeout=3)
    finally:
        disconnect.set()
        await asyncio.wait_for(task, timeout=3)

    assert headers["content-type"].startswith("text/event-stream")
    frames = [line for line in b"".join(chunks).decode().splitlines() if line.startswith("data:")]
    payloads = [json.loads(line[5:].strip()) for line in frames]
    assert payloads[0]["type"] == "hello" and payloads[0]["sid"] == sid
    toasts = [p for p in payloads if p["type"] == "toast"]
    assert toasts == [{"type": "toast", "level": "success", "text": "sse works", "ts": toasts[0]["ts"]}]
    assert get_event_bus().subscriber_count(sid) == 0  # the generator was closed on disconnect
