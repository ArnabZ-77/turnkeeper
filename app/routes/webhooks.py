"""POST /webhooks/paypal (always 200 after persisting) and GET /api/debug/webhooks (last 20 rows)."""

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlmodel import select

from app.db import session_scope
from app.engine.common import iso
from app.engine.webhooks import handle_event
from app.models import WebhookEvent

log = logging.getLogger("turnkeeper.routes.webhooks")

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        result = await handle_event(request.headers, body)
    except Exception as e:  # noqa: BLE001 - PayPal retries on non-2xx; a crash here must not loop forever
        log.exception("webhook handling crashed")
        result = {"ok": False, "error": type(e).__name__}
    return JSONResponse(result, status_code=200)


@router.get("/api/debug/webhooks")
async def debug_webhooks() -> list[dict[str, Any]]:
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
