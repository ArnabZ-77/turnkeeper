"""POST /webhooks/paypal (always 200 after persisting) and GET /api/debug/webhooks (last 20 rows).

The debug listing exposes resource ids, so on a deployed (https PUBLIC_BASE_URL) host it requires
`Authorization: Bearer <MCP_DEMO_TOKEN>`; local http deployments stay open for the f_webhook smoke test.
"""

import hmac
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.engine.common import iso
from app.engine.webhooks import PAYLOAD_LIMIT, handle_event
from app.mcp.auth import RateLimiter, client_ip
from app.models import WebhookEvent

log = logging.getLogger("turnkeeper.routes.webhooks")

router = APIRouter(tags=["webhooks"])

# The endpoint is public (PayPal posts to it) and every delivery can cost a verify-webhook-signature call from
# the shared PayPal budget, so intake is bounded in size and in rate per source address.
WEBHOOK_LIMITER = RateLimiter(limit=120, window=60.0)


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """The request body, or None when it is larger than `limit` (never buffers more than limit + one chunk)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request) -> JSONResponse:
    allowed, retry_after = WEBHOOK_LIMITER.check(f"webhook|{client_ip(request.scope)}")
    if not allowed:
        return JSONResponse(
            {"ok": False, "error": "rate limited"},
            status_code=429,
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    body = await _read_capped(request, PAYLOAD_LIMIT)
    if body is None:
        return JSONResponse({"ok": False, "error": "payload too large"}, status_code=413)
    try:
        result = await handle_event(request.headers, body)
    except Exception as e:  # noqa: BLE001 - PayPal retries on non-2xx; a crash here must not loop forever
        log.exception("webhook handling crashed")
        result = {"ok": False, "error": type(e).__name__}
    return JSONResponse(result, status_code=200)


def _require_debug_access(request: Request) -> None:
    settings = get_settings()
    if not settings.public_base_url.lower().startswith("https://"):
        return  # local development
    expected = (settings.mcp_demo_token or "").strip()
    scheme, _, presented = (request.headers.get("authorization") or "").strip().partition(" ")
    presented = presented.strip()
    if not expected or scheme.lower() != "bearer" or not presented:
        raise HTTPException(
            status_code=401, detail="debug endpoint needs Authorization: Bearer <MCP demo token>"
        )
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(
            status_code=401, detail="debug endpoint needs Authorization: Bearer <MCP demo token>"
        )


@router.get("/api/debug/webhooks")
async def debug_webhooks(request: Request) -> list[dict[str, Any]]:
    _require_debug_access(request)
    with session_scope() as db:
        rows = db.exec(select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(20)).all()  # type: ignore[attr-defined]
    return [
        {
            "id": r.id,
            "event_id": r.event_id,
            "event_type": r.event_type,
            "resource_id": r.resource_id,
            "verified": r.verified,
            "received_at": iso(r.received_at),
        }
        for r in rows
    ]
