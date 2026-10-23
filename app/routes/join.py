"""Member join flow (phone): pre-consent charter card -> Vault setup token -> approve -> payment token.

GET  /api/circles/{circle_id}/invites   -> signed invite tokens per member (organizer, needs the session)
GET  /api/join/{token}                  -> charter card data for the pre-consent page
POST /api/join/{token}/setup            -> creates the setup token, returns the PayPal approve URL (503 without B1)
GET  /join/{token}/return               -> creates the payment token, stores vault_id, redirects to the app
GET  /join/{token}/cancel               -> redirects to the app
"""

import hashlib
import hmac
import importlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.engine import sessions, state
from app.engine.common import money_str
from app.models import MemberRow

log = logging.getLogger("turnkeeper.routes.join")

router = APIRouter(tags=["join"])


def _sig(circle_id: str, handle: str) -> str:
    key = get_settings().session_secret.encode("utf-8")
    return hmac.new(key, f"{circle_id}.{handle}".encode(), hashlib.sha256).hexdigest()[:16]


def invite_token(circle_id: str, handle: str) -> str:
    return f"{circle_id}.{handle}.{_sig(circle_id, handle)}"


def parse_invite(token: str) -> tuple[str, str]:
    parts = token.split(".")
    if len(parts) != 3 or not hmac.compare_digest(parts[2], _sig(parts[0], parts[1])):
        raise HTTPException(status_code=404, detail="invalid invite")
    return parts[0], parts[1]


def _member(circle_id: str, handle: str) -> MemberRow:
    with session_scope() as db:
        row = db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown member")
    return row


def _vaulted(row: MemberRow) -> bool:
    return bool(row.vault_id) and not row.vault_id.startswith("VAULT_ID_")


def _charter_card(circle_id: str) -> dict[str, Any]:
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    n = len(charter.members)
    return {
        "id": circle.id,
        "name": charter.name,
        "amount": money_str(charter.amount),
        "currency": charter.currency,
        "cadence": charter.cadence,
        "cycles": n,
        "start_date": charter.start_date.isoformat(),
        "fee_policy": charter.fee_policy,
        "fee_note": "PayPal's standard fee is deducted from each contribution; the pot is paid net of fees.",
        "charter_sha256": circle.charter_sha256,
        "buffer_seed": money_str(charter.buffer.seed),
        "quorum": charter.quorum,
    }


@router.get("/api/circles/{circle_id}/invites")
async def invites(circle_id: str, sess: CurrentSession) -> list[dict[str, Any]]:
    sessions.owns_circle(sess, circle_id)
    base = get_settings().public_base_url.rstrip("/")
    out = []
    for m in state.members_of(circle_id):
        token = invite_token(circle_id, m.handle)
        out.append(
            {
                "handle": m.handle,
                "name": m.name,
                "token": token,
                "url": f"{base}/join/{token}",
                "vaulted": _vaulted(m),
            }
        )
    return out


@router.get("/api/join/{token}")
async def join_info(token: str) -> dict[str, Any]:
    circle_id, handle = parse_invite(token)
    try:
        card = _charter_card(circle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="circle not found") from None
    m = _member(circle_id, handle)
    return {
        "ok": True,
        "circle": card,
        "member": {"handle": m.handle, "name": m.name, "lang": m.lang},
        "vaulted": _vaulted(m),
    }


def _paypal_modules() -> tuple[Any, Any, Any]:
    try:
        vault = importlib.import_module("app.paypal.vault")
        gw = importlib.import_module("app.paypal.gateway").get_gateway()
        keys = importlib.import_module("app.paypal.keys")
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"PayPal vault wrapper not available: {e}") from None
    return vault, gw, keys


def _is_paypal_error(e: BaseException) -> bool:
    return hasattr(e, "issue_codes") and hasattr(e, "name")


@router.post("/api/join/{token}/setup")
async def join_setup(token: str) -> dict[str, Any]:
    circle_id, handle = parse_invite(token)
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    _member(circle_id, handle)
    vault, gw, keys = _paypal_modules()
    try:
        billing_plan = importlib.import_module("app.domain.billing_plan").billing_plan_from_charter(charter)
    except ImportError:
        billing_plan = None
    base = get_settings().public_base_url.rstrip("/")
    cadence = "monthly" if charter.cadence == "monthly" else "weekly"
    description = (
        f"Turnkeeper · {charter.name} · ${money_str(charter.amount)} {cadence} × {len(charter.members)}"
    )
    try:
        data = await vault.create_setup_token(
            gw,
            request_id=keys.setup_request_id(circle.run_id, circle_id, handle),
            description=description,
            billing_plan=billing_plan,
            return_url=f"{base}/join/{token}/return",
            cancel_url=f"{base}/join/{token}/cancel",
        )
    except Exception as e:  # noqa: BLE001 - PayPal errors become 502, anything else propagates
        if not _is_paypal_error(e):
            raise
        raise HTTPException(
            status_code=502,
            detail={"error": str(getattr(e, "name", "")), "issues": list(getattr(e, "issue_codes", []))},
        ) from None
    setup_id = data.get("id")
    with session_scope() as db:
        row = db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()
        if row is not None:
            row.setup_token_id = setup_id
            db.add(row)
    return {"ok": True, "setup_token_id": setup_id, "approve_url": vault.approve_link(data)}


@router.get("/join/{token}/return")
async def join_return(token: str, approval_token_id: str | None = None) -> RedirectResponse:
    circle_id, handle = parse_invite(token)
    circle = state.load_circle(circle_id)
    row = _member(circle_id, handle)
    setup_id = approval_token_id or row.setup_token_id
    if not setup_id:
        return RedirectResponse(f"/?join=error&member={handle}", status_code=303)
    vault, gw, keys = _paypal_modules()
    try:
        data = await vault.create_payment_token(
            gw,
            setup_token_id=setup_id,
            request_id=f"{keys.setup_request_id(circle.run_id, circle_id, handle)}:pt",
        )
    except Exception as e:  # noqa: BLE001
        if not _is_paypal_error(e):
            raise
        log.warning("payment token creation failed for %s: %s", handle, getattr(e, "name", ""))
        return RedirectResponse(f"/?join=error&member={handle}", status_code=303)
    with session_scope() as db:
        m = db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()
        if m is not None:
            m.vault_id = data.get("id")
            m.customer_id = (data.get("customer") or {}).get("id")
            m.setup_token_id = None
            db.add(m)
    publish = sessions.circle_publisher(circle_id)
    await publish(
        {"type": "toast", "level": "success", "text": f"{handle} joined and vaulted a payment method"}
    )
    await publish({"type": "board"})
    return RedirectResponse(f"/?joined={handle}", status_code=303)


@router.get("/join/{token}/cancel")
async def join_cancel(token: str) -> RedirectResponse:
    _circle_id, handle = parse_invite(token)
    return RedirectResponse(f"/?join=cancelled&member={handle}", status_code=303)
