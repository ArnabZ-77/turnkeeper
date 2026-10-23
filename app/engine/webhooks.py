"""Webhook reconciler: verify, dedupe, update contributions/payouts, trigger the mandate payout.

`handle_event(headers, body)` is called by POST /webhooks/paypal. Verification uses B1's
verify_signature when settings.paypal_webhook_id is set; otherwise events are stored unverified.
Full webhook bodies are stored (payload column) but never logged.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.engine import state
from app.engine.common import money, utcnow
from app.engine.events import Publish
from app.engine.sessions import circle_publisher
from app.models import Contribution, LedgerRow, MemberRow, Round, WebhookEvent

log = logging.getLogger("turnkeeper.engine.webhooks")

PAYLOAD_LIMIT = 64_000


class _CIHeaders(dict):
    """Case-insensitive view over the request headers (PayPal uses PAYPAL-* names)."""

    def __init__(self, headers: Mapping[str, str] | None) -> None:
        super().__init__()
        for k, v in (headers or {}).items():
            self[str(k)] = v

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key.lower(), value)

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key.lower())

    def __contains__(self, key: object) -> bool:
        return super().__contains__(str(key).lower())

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key.lower(), default)


async def _verify(headers: Mapping[str, str], body: bytes, webhook_id: str) -> bool:
    """B1's verify_signature through the shared gateway. Any failure counts as unverified."""
    try:
        wh = importlib.import_module("app.paypal.webhooks")
        gw = importlib.import_module("app.paypal.gateway").get_gateway()
        return bool(await wh.verify_signature(gw, headers=headers, body=body, webhook_id=webhook_id))
    except Exception as e:  # noqa: BLE001 - never let verification crash intake
        log.warning("webhook verification failed: %s", type(e).__name__)
        return False


def _store(event_id: str, event_type: str, resource_id: str | None, verified: bool, body: bytes) -> bool:
    """Insert the WebhookEvent row; False when the event id was already stored (duplicate)."""
    payload = body.decode("utf-8", "replace")[:PAYLOAD_LIMIT]
    try:
        with session_scope() as db:
            existing = db.exec(select(WebhookEvent.id).where(WebhookEvent.event_id == event_id)).first()
            if existing is not None:
                return False
            db.add(
                WebhookEvent(
                    event_id=event_id,
                    event_type=event_type,
                    resource_id=resource_id,
                    verified=verified,
                    received_at=utcnow(),
                    payload=payload,
                )
            )
    except IntegrityError:
        return False
    return True


def _find_contribution_by_capture(capture_id: str) -> Contribution | None:
    with session_scope() as db:
        return db.exec(select(Contribution).where(Contribution.capture_id == capture_id)).first()


def _find_round_by_item(item_id: str) -> Round | None:
    with session_scope() as db:
        return db.exec(select(Round).where(Round.payout_item_id == item_id)).first()


def _chip(c: Contribution) -> dict[str, Any]:
    return {
        "type": "chip",
        "round_n": c.round_n,
        "member": c.member_handle,
        "status": c.status,
        "order_id": c.order_id,
        "capture_id": c.capture_id,
        "label": c.label,
        "decline_code": c.decline_code,
        "webhook_verified": c.webhook_verified,
        "predicted": c.predicted,
    }


def _round_fully_verified(circle_id: str, round_n: int) -> bool:
    charter = state.load_charter(circle_id)
    contribs = {c.member_handle: c for c in state.round_contributions(circle_id, round_n)}
    for m in charter.members:
        c = contribs.get(m.handle)
        if c is None or c.status not in state.SETTLED_CONTRIBUTION:
            return False
        if c.status == "COMPLETED" and not c.webhook_verified:
            return False
    return True


async def _on_capture_completed(resource: dict, verified: bool, publish_for: Any) -> dict[str, Any]:
    capture_id = str(resource.get("id") or "")
    contrib = _find_contribution_by_capture(capture_id) if capture_id else None
    if contrib is None:
        return {"handled": False, "reason": "capture not ours"}
    breakdown = resource.get("seller_receivable_breakdown") or {}
    fee_value = (breakdown.get("paypal_fee") or {}).get("value")
    net_value = (breakdown.get("net_amount") or {}).get("value")
    gross_value = (breakdown.get("gross_amount") or {}).get("value") or (resource.get("amount") or {}).get(
        "value"
    )
    with session_scope() as db:
        row = db.get(Contribution, contrib.id)
        if row is None:
            return {"handled": False, "reason": "contribution vanished"}
        if row.status != "COMPLETED":
            row.status = "COMPLETED"
            row.decline_code = None
            row.decline_source = None
        if verified:
            row.webhook_verified = True
            row.label = "Webhook verified"
        if gross_value:
            row.gross = str(money(gross_value))
        if fee_value:
            row.fee = str(money(fee_value))
        if net_value:
            new_net = money(net_value)
            ledger = db.exec(
                select(LedgerRow).where(
                    LedgerRow.circle_id == row.circle_id,
                    LedgerRow.kind == "contribution_net",
                    LedgerRow.ref == capture_id,
                )
            ).first()
            if ledger is None:
                db.add(
                    LedgerRow(
                        circle_id=row.circle_id,
                        kind="contribution_net",
                        amount=str(new_net),
                        round_n=row.round_n,
                        member_handle=row.member_handle,
                        ref=capture_id,
                        ts=utcnow(),
                    )
                )
            elif money(ledger.amount) != new_net:
                ledger.amount = str(new_net)
                db.add(ledger)
            row.net = str(new_net)
        row.updated_at = utcnow()
        db.add(row)
        contrib = row
    publish = publish_for(contrib.circle_id)
    await publish(_chip(contrib))
    if verified:
        await publish(
            {"type": "toast", "level": "info", "text": f"{contrib.member_handle}: capture webhook verified"}
        )
    triggered = None
    if (
        verified
        and _round_fully_verified(contrib.circle_id, contrib.round_n)
        and state.mandate_present(contrib.circle_id)
    ):
        rnd = state.get_round(contrib.circle_id, contrib.round_n)
        if rnd is not None and rnd.status != "PAID" and not state.live_payout_exists(rnd):
            from app.engine.rounds import pay_round  # lazy: rounds imports state, keep the graph acyclic

            await publish(
                {
                    "type": "toast",
                    "level": "info",
                    "text": f"Round {contrib.round_n}: last capture verified, mandate fires",
                }
            )
            triggered = await pay_round(contrib.circle_id, contrib.round_n, publish=publish)
    return {
        "handled": True,
        "circle_id": contrib.circle_id,
        "round_n": contrib.round_n,
        "member": contrib.member_handle,
        "payout": triggered,
    }


async def _on_capture_denied(resource: dict, publish_for: Any) -> dict[str, Any]:
    capture_id = str(resource.get("id") or "")
    contrib = _find_contribution_by_capture(capture_id) if capture_id else None
    if contrib is None:
        return {"handled": False, "reason": "capture not ours"}
    with session_scope() as db:
        row = db.get(Contribution, contrib.id)
        if row is None:
            return {"handled": False}
        row.status = "DECLINED"
        row.decline_code = str(resource.get("status_details", {}).get("reason") or "CAPTURE_DENIED")
        row.decline_source = "webhook"
        row.label = f"Capture denied: {row.decline_code}"
        row.updated_at = utcnow()
        db.add(row)
        ledger = db.exec(
            select(LedgerRow).where(LedgerRow.kind == "contribution_net", LedgerRow.ref == capture_id)
        ).first()
        if ledger is not None:
            db.delete(ledger)
        contrib = row
    publish = publish_for(contrib.circle_id)
    await publish(_chip(contrib))
    await publish(
        {"type": "toast", "level": "warn", "text": f"{contrib.member_handle}: capture denied (webhook)"}
    )
    return {
        "handled": True,
        "circle_id": contrib.circle_id,
        "round_n": contrib.round_n,
        "member": contrib.member_handle,
    }


_PAYOUT_ITEM_STATUS = {
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED": "SUCCESS",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED": "UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.FAILED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.BLOCKED": "BLOCKED",
    "PAYMENT.PAYOUTS-ITEM.CANCELED": "CANCELED",
    "PAYMENT.PAYOUTS-ITEM.DENIED": "DENIED",
    "PAYMENT.PAYOUTS-ITEM.HELD": "ONHOLD",
    "PAYMENT.PAYOUTS-ITEM.REFUNDED": "REFUNDED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED": "RETURNED",
}


async def _on_payout_item(event_type: str, resource: dict, publish_for: Any) -> dict[str, Any]:
    item_id = str(resource.get("payout_item_id") or resource.get("id") or "")
    rnd = _find_round_by_item(item_id) if item_id else None
    if rnd is None:
        return {"handled": False, "reason": "payout item not ours"}
    status = (
        _PAYOUT_ITEM_STATUS.get(event_type)
        or str(resource.get("transaction_status") or "").upper()
        or "PENDING"
    )
    now = utcnow()
    with session_scope() as db:
        row = db.get(Round, rnd.id)
        if row is None:
            return {"handled": False}
        row.payout_status = status
        if status == "SUCCESS" and row.status != "PAID":
            row.status = "PAID"
            row.paid_at = now
        db.add(row)
        rnd = row
    publish = publish_for(rnd.circle_id)
    await publish(
        {
            "type": "payout",
            "round_n": rnd.n,
            "status": status,
            "batch_id": rnd.payout_batch_id,
            "item_id": item_id,
            "amount": rnd.payout_amount,
            "recipient": rnd.recipient_handle,
            "wallet": _wallet_of(rnd.circle_id, rnd.recipient_handle),
        }
    )
    await publish({"type": "board"})
    level = (
        "success"
        if status == "SUCCESS"
        else ("warn" if status in ("UNCLAIMED", "FAILED", "RETURNED", "BLOCKED") else "info")
    )
    await publish({"type": "toast", "level": level, "text": f"Round {rnd.n} payout {status} (webhook)"})
    return {"handled": True, "circle_id": rnd.circle_id, "round_n": rnd.n, "status": status}


def _wallet_of(circle_id: str, handle: str) -> str:
    with session_scope() as db:
        m = db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()
    return m.payout_wallet if m else "PAYPAL"


async def _on_vault_token(event_type: str, resource: dict, publish_for: Any) -> dict[str, Any]:
    vault_id = str(resource.get("id") or "")
    with session_scope() as db:
        members = (
            list(db.exec(select(MemberRow).where(MemberRow.vault_id == vault_id)).all()) if vault_id else []
        )
        if event_type == "VAULT.PAYMENT-TOKEN.DELETED":
            for m in members:
                m.vault_id = None
                db.add(m)
    if not members:
        return {"handled": False, "reason": "vault token not ours"}
    for m in members:
        publish = publish_for(m.circle_id)
        if event_type == "VAULT.PAYMENT-TOKEN.DELETED":
            await publish(
                {
                    "type": "toast",
                    "level": "warn",
                    "text": f"{m.handle}: consent revoked (vault token deleted)",
                }
            )
        else:
            await publish(
                {"type": "toast", "level": "success", "text": f"{m.handle}: vault token confirmed by webhook"}
            )
        await publish({"type": "board"})
    return {"handled": True, "members": [m.handle for m in members]}


async def handle_event(headers: Mapping[str, str], body: bytes, *, publish_for: Any | None = None) -> dict:
    """Verify, dedupe, persist and dispatch one PayPal webhook delivery. Never raises on bad input."""
    publish_for = publish_for or circle_publisher
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        log.warning("webhook body is not JSON")
        return {"ok": False, "error": "invalid json"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "unexpected body"}
    event_id = str(data.get("id") or "")
    event_type = str(data.get("event_type") or "")
    resource = data.get("resource") if isinstance(data.get("resource"), dict) else {}
    resource_id = str(resource.get("id") or resource.get("payout_item_id") or "") or None
    if not event_id or not event_type:
        return {"ok": False, "error": "missing id or event_type"}

    settings = get_settings()
    webhook_id = settings.paypal_webhook_id
    verified = bool(webhook_id) and await _verify(_CIHeaders(headers), body, webhook_id)
    if not _store(event_id, event_type, resource_id, verified, body):
        log.info("webhook %s duplicate (%s)", event_id, event_type)
        return {
            "ok": True,
            "duplicate": True,
            "event_id": event_id,
            "event_type": event_type,
            "verified": verified,
        }
    log.info("webhook %s %s verified=%s", event_type, event_id, verified)

    if webhook_id and not verified:
        # A webhook id is configured, so an event that fails verification is untrusted: store it for the
        # debug view but never let it change contributions, payouts or vault state.
        log.warning("webhook %s %s failed verification; not dispatched", event_type, event_id)
        return {
            "ok": True,
            "duplicate": False,
            "event_id": event_id,
            "event_type": event_type,
            "verified": False,
            "handled": False,
            "rejected": "signature verification failed",
        }

    result: dict[str, Any] = {
        "ok": True,
        "duplicate": False,
        "event_id": event_id,
        "event_type": event_type,
        "verified": verified,
        "handled": False,
    }
    try:
        if event_type == "PAYMENT.CAPTURE.COMPLETED":
            result.update(await _on_capture_completed(resource, verified, publish_for))
        elif event_type == "PAYMENT.CAPTURE.DENIED":
            result.update(await _on_capture_denied(resource, publish_for))
        elif event_type.startswith("PAYMENT.PAYOUTS-ITEM."):
            result.update(await _on_payout_item(event_type, resource, publish_for))
        elif event_type.startswith("VAULT.PAYMENT-TOKEN."):
            result.update(await _on_vault_token(event_type, resource, publish_for))
        elif event_type == "PAYMENT.PAYOUTSBATCH.SUCCESS":
            result.update(
                {"handled": True, "batch_id": (resource.get("batch_header") or {}).get("payout_batch_id")}
            )
    except Exception as e:  # noqa: BLE001 - stored already; report and let PayPal move on
        log.exception("webhook dispatch failed for %s", event_type)
        result.update({"handled": False, "error": type(e).__name__})
    return result


def publisher_for(circle_id: str) -> Publish:
    """Exposed for tests and the reconciler's polling fallback."""
    return circle_publisher(circle_id)
