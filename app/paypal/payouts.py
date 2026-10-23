"""Payouts v1: pay the pot to the recipient's PayPal (EMAIL) or Venmo (PHONE) wallet.

Field names confirmed against the Payouts v1.9 OpenAPI schema (2026-10-22): `sender_batch_header.{sender_batch_id,
email_subject, email_message}`, `items[].{recipient_type: EMAIL|PHONE|PAYPAL_ID, receiver, recipient_wallet:
PAYPAL|VENMO, amount.{value, currency}, note, sender_item_id}`; note the amount key is `currency`, not
`currency_code`. Item `transaction_status` is one of SUCCESS | FAILED | PENDING | UNCLAIMED | RETURNED | ONHOLD |
BLOCKED | REFUNDED | REVERSED; batch `batch_status` is DENIED | PENDING | PROCESSING | SUCCESS | CANCELED.
PayPal rejects a `sender_batch_id` reused within 30 days (and a 5xx may be retried with the same id safely).
"""

from __future__ import annotations

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
