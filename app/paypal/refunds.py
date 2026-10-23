"""Payments v2 refunds (dissolve, void, the weekly canary). The Python toolkit has no refund tool.

Confirmed against the Payments v2.12 OpenAPI schema (2026-10-22): `POST /v2/payments/captures/{capture_id}/refund`
with an optional body `{"amount": {"currency_code", "value"}, "note_to_payer", "invoice_id", "custom_id"}`; an
empty body refunds the full capture. Response: `{"id", "status": COMPLETED|PENDING|FAILED|CANCELLED, "amount", ...}`.
"""

from __future__ import annotations

from decimal import Decimal

from app.paypal._util import check_len, money_str
from app.paypal.gateway import Gateway

CAPTURES_PATH = "/v2/payments/captures"
REFUNDS_PATH = "/v2/payments/refunds"


async def refund_capture(
    gw: Gateway,
    *,
    capture_id: str,
    request_id: str,
    amount: Decimal | None = None,
    note: str = "",
    currency: str = "USD",
    invoice_id: str | None = None,
) -> dict:
    """Refund a capture, fully (amount=None -> empty JSON body) or partially. Idempotent via `request_id`."""
    body: dict = {}
    if amount is not None:
        body["amount"] = {"currency_code": currency, "value": money_str(amount)}
    if note:
        body["note_to_payer"] = check_len(note, 255, "note_to_payer")
    if invoice_id:
        body["invoice_id"] = check_len(invoice_id, 127, "invoice_id")
    resp = await gw.call("POST", f"{CAPTURES_PATH}/{capture_id}/refund", json=body, request_id=request_id)
    return resp.data


async def get_refund(gw: Gateway, *, refund_id: str) -> dict:
    resp = await gw.call("GET", f"{REFUNDS_PATH}/{refund_id}")
    return resp.data


def refund_status(refund: dict) -> str:
    return str(refund.get("status") or "UNKNOWN")
