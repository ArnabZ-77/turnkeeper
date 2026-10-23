"""Payouts v1: pay the pot to the recipient's PayPal (EMAIL) or Venmo (PHONE) wallet.

Field names confirmed against the Payouts v1.9 OpenAPI schema (2026-10-22): `sender_batch_header.{sender_batch_id,
email_subject, email_message}`, `items[].{recipient_type: EMAIL|PHONE|PAYPAL_ID, receiver, recipient_wallet:
PAYPAL|VENMO, amount.{value, currency}, note, sender_item_id}`; note the amount key is `currency`, not
`currency_code`. Item `transaction_status` is one of SUCCESS | FAILED | PENDING | UNCLAIMED | RETURNED | ONHOLD |
BLOCKED | REFUNDED | REVERSED; batch `batch_status` is DENIED | PENDING | PROCESSING | SUCCESS | CANCELED.
PayPal rejects a `sender_batch_id` reused within 30 days (and a 5xx may be retried with the same id safely).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_validator

from app.paypal._util import check_len, money_str, to_decimal
from app.paypal.gateway import Gateway
from app.paypal.keys import MAX_SENDER_BATCH_ID_LEN, MAX_SENDER_ITEM_ID_LEN

PAYOUTS_PATH = "/v1/payments/payouts"
PAYOUT_ITEM_PATH = "/v1/payments/payouts-item"
LIVE_ITEM_STATUSES = frozenset({"PENDING", "UNCLAIMED", "ONHOLD"})
FINAL_ITEM_STATUSES = frozenset({"SUCCESS", "FAILED", "RETURNED", "BLOCKED", "REFUNDED", "REVERSED"})
FINAL_BATCH_STATUSES = frozenset({"SUCCESS", "DENIED", "CANCELED"})


class PayoutItem(BaseModel):
    recipient_type: Literal["EMAIL", "PHONE", "PAYPAL_ID"] = "EMAIL"
    receiver: str
    wallet: Literal["PAYPAL", "VENMO"] = "PAYPAL"
    amount: Decimal
    currency: str = "USD"
    note: str = ""
    sender_item_id: str

    @field_validator("amount")
    @classmethod
    def _positive_two_places(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("payout amount must be positive")
        return Decimal(money_str(v))

    @field_validator("receiver")
    @classmethod
    def _receiver_len(cls, v: str) -> str:
        return check_len(v.strip(), 127, "receiver")

    @field_validator("sender_item_id")
    @classmethod
    def _item_id_len(cls, v: str) -> str:
        return check_len(v, MAX_SENDER_ITEM_ID_LEN, "sender_item_id")

    @field_validator("note")
    @classmethod
    def _note_len(cls, v: str) -> str:
        return check_len(v, 4000, "note")

    def to_wire(self) -> dict:
        item = {
            "recipient_type": self.recipient_type,
            "receiver": self.receiver,
            "recipient_wallet": self.wallet,
            "amount": {"value": money_str(self.amount), "currency": self.currency},
            "sender_item_id": self.sender_item_id,
        }
        if self.note:
            item["note"] = self.note
        return item


async def create_payout(
    gw: Gateway,
    *,
    sender_batch_id: str,
    items: list[PayoutItem],
    email_subject: str,
    email_message: str | None = None,
    request_id: str | None = None,
) -> dict:
    """POST /v1/payments/payouts. The batch id is the idempotency key (and the PayPal-Request-Id unless given)."""
    if not items:
        raise ValueError("a payout needs at least one item")
    check_len(sender_batch_id, MAX_SENDER_BATCH_ID_LEN, "sender_batch_id")
    header: dict = {
        "sender_batch_id": sender_batch_id,
        "email_subject": check_len(email_subject, 255, "email_subject"),
    }
    if email_message:
        header["email_message"] = check_len(email_message, 1000, "email_message")
    body = {"sender_batch_header": header, "items": [item.to_wire() for item in items]}
    resp = await gw.call("POST", PAYOUTS_PATH, json=body, request_id=request_id or sender_batch_id)
    return resp.data


async def get_payout_batch(gw: Gateway, *, batch_id: str) -> dict:
    """GET /v1/payments/payouts/{batch_id} -> batch_header + items[] (with transaction_status and payout_item_fee)."""
    resp = await gw.call("GET", f"{PAYOUTS_PATH}/{batch_id}")
    return resp.data


async def get_payout_item(gw: Gateway, *, item_id: str) -> dict:
    resp = await gw.call("GET", f"{PAYOUT_ITEM_PATH}/{item_id}")
    return resp.data


async def cancel_payout_item(gw: Gateway, *, item_id: str) -> dict:
    """POST /v1/payments/payouts-item/{id}/cancel: only UNCLAIMED items can be cancelled (funds return to the sender)."""
    resp = await gw.call("POST", f"{PAYOUT_ITEM_PATH}/{item_id}/cancel")
    return resp.data


def first_item(batch: dict) -> dict | None:
    items = batch.get("items") or []
    return items[0] if items and isinstance(items[0], dict) else None


def item_status(item: dict | None) -> str:
    """PayPal's transaction_status verbatim ("SUCCESS" | "UNCLAIMED" | "PENDING" | "FAILED" | ...), "UNKNOWN" if absent."""
    if not item:
        return "UNKNOWN"
    return str(item.get("transaction_status") or "UNKNOWN")


def batch_status(batch: dict) -> str:
    header = batch.get("batch_header") or {}
    return str(header.get("batch_status") or "UNKNOWN")


def batch_id_of(batch: dict) -> str | None:
    header = batch.get("batch_header") or {}
    value = header.get("payout_batch_id")
    return str(value) if value else None


def item_id_of(item: dict | None) -> str | None:
    value = (item or {}).get("payout_item_id")
    return str(value) if value else None


def item_fee(item: dict | None) -> Decimal | None:
    return to_decimal((item or {}).get("payout_item_fee"))


def batch_fees(batch: dict) -> Decimal | None:
    return to_decimal((batch.get("batch_header") or {}).get("fees"))


# --- D1 finding (2026-10-23): cancelling an UNCLAIMED item in the sandbox --------------------------------
# POST .../payouts-item/{id}/cancel answered 400 BATCH_NOT_COMPLETED while the batch was PROCESSING and 500
# INTERNAL_SERVICE_ERROR (twice) once it was SUCCESS. So: wait for the batch to finish (bounded), try the cancel
# exactly once, and on any failure say so honestly and re-route with a new batch; never claim a cancel worked.

CANCEL_UNAVAILABLE_LABEL = "Cancel not available in sandbox"
BATCH_WAIT_BACKOFF_S = (2.0, 4.0, 8.0, 15.0)


async def wait_for_batch_final(
    gw: Gateway,
    *,
    batch_id: str,
    max_wait_s: float = 60.0,
    backoff_s: tuple[float, ...] = BATCH_WAIT_BACKOFF_S,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    fetch: Callable[..., Awaitable[dict]] | None = None,
) -> tuple[dict | None, bool]:
    """Poll GET /v1/payments/payouts/{batch_id} with backoff until batch_status is final or `max_wait_s` passes.

    Returns (last batch seen or None, final?). Read errors are tolerated (the wait simply continues); the last
    backoff value repeats. Never raises for PayPal errors. `fetch` replaces get_payout_batch (engine fakes)."""
    fetch = fetch or get_payout_batch
    started = clock()
    last: dict | None = None
    attempt = 0
    while True:
        try:
            last = await fetch(gw, batch_id=batch_id)
            if batch_status(last) in FINAL_BATCH_STATUSES:
                return last, True
        except Exception:  # noqa: BLE001 - a failed read only means "not known to be final yet"
            pass
        elapsed = clock() - started
        if elapsed >= max_wait_s or attempt >= 100:  # hard cap even if the clock misbehaves
            return last, False
        delay = backoff_s[min(attempt, len(backoff_s) - 1)] if backoff_s else 1.0
        await sleep(min(delay, max(0.0, max_wait_s - elapsed)))
        attempt += 1
