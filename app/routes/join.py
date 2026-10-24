"""Member join flow (phone): pre-consent charter card -> Vault setup token -> PayPal approve -> payment token.

GET  /api/circles/{circle_id}/invites   -> signed, expiring invite links per member (organizer session)
POST /api/join/judge-circle             -> the session's $5 two-member judge mini-circle + judge_member's invite
GET  /api/join/{invite}                 -> charter card data for the pre-consent page (fees line, billing plan,
                                           the member's payout turn, join status and message)
POST /api/join/{invite}/start           -> setup token (RECURRING_PREPAID + billing_plan_from_charter), approve URL
POST /api/join/{invite}/setup           -> alias of /start (M1 name)
GET  /join/{invite}/return              -> payment token, vault_id/customer_id stored -> 303 /join/{invite}?status=...
GET  /join/{invite}/cancel              -> 303 /join/{invite}?status=cancelled
POST /api/join/{invite}/wallet          -> payout wallet + receiver (PayPal only; Venmo is cut in this sandbox demo).
                                           Vaulted members only; changing a receiver on file is allowed within
                                           jn.WALLET_CHANGE_WINDOW of joining, else 409 (the organizer changes it)

The SPA renders /join/{invite} (web side) from GET /api/join/{invite}; ``status`` in the redirect is one of
vaulted | cancelled | action_required | declined | error | expired | invalid.
"""

import importlib
import json
import logging
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import session_scope
from app.engine import join as jn
from app.engine import sessions, state
from app.engine.common import as_utc, iso, money_str, utcnow
from app.engine.sessions import require_session
from app.mcp.auth import RateLimiter, client_ip
from app.models import AgentTrace, MemberRow, Session

CurrentSession = Annotated[Session, Depends(require_session)]

log = logging.getLogger("turnkeeper.routes.join")

router = APIRouter(tags=["join"])

_EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")

# PayPal setup tokens per caller IP per hour (on top of jn.MAX_SETUP_ATTEMPTS per invite): every start is a call
# on the shared 40/min FIFO gateway, so an anonymous script must not be able to fill it.
SETUP_TOKENS_PER_IP_HOUR = 10
SETUP_LIMITER = RateLimiter(limit=SETUP_TOKENS_PER_IP_HOUR, window=3600.0)


# --- invite helpers (names kept from M1) ------------------------------------------------------------------


def invite_token(circle_id: str, handle: str) -> str:
    return jn.make_invite(circle_id, handle)


def _invite(token: str, *, allow_expired: bool = False) -> jn.Invite:
    try:
        return jn.parse_invite(token, allow_expired=allow_expired)
    except jn.InviteError as e:
        code = {"expired": 410, "unavailable": 503}.get(e.kind, 404)
        raise HTTPException(status_code=code, detail=e.message) from None


def _invite_redirect(token: str, *, allow_expired: bool = True) -> jn.Invite | RedirectResponse:
    """For the browser routes PayPal redirects to: a link that no longer verifies (revoked when the member
    vaulted, or tampered) lands on the SPA's join page with a status, never on a raw JSON 404."""
    try:
        return jn.parse_invite(token, allow_expired=allow_expired)
    except jn.InviteError as e:
        return _back(
            token if _SAFE_TOKEN_RX.match(token or "") else "invalid", _INVITE_STATUS.get(e.kind, "invalid")
        )


_SAFE_TOKEN_RX = re.compile(r"^[a-z0-9._]{1,80}$")
_INVITE_STATUS = {"expired": "expired", "unavailable": "error"}


def parse_invite(token: str) -> tuple[str, str]:
    inv = _invite(token)
    return inv.circle_id, inv.handle


def _member(circle_id: str, handle: str) -> MemberRow:
    row = jn.get_member(circle_id, handle)
    if row is None:
        raise HTTPException(
            status_code=404, detail="This invite link is not valid. Ask the organizer for a new one."
        )
    return row


def _vaulted(row: MemberRow) -> bool:
    return jn.is_vaulted(row)


def _circle_and_charter(circle_id: str) -> tuple[Any, Any]:
    try:
        circle = state.load_circle(circle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="circle not found") from None
    return circle, state.charter_of(circle)


def _charter_card(circle_id: str, viewer: str | None = None) -> dict[str, Any]:
    circle, charter = _circle_and_charter(circle_id)
    fees = jn.fees_summary(charter)
    return {
        "id": circle.id,
        "name": charter.name,
        "amount": money_str(charter.amount),
        "currency": charter.currency,
        "cadence": charter.cadence,
        "cycles": len(charter.members),
        "start_date": charter.start_date.isoformat(),
        "fee_policy": charter.fee_policy,
        "fee_note": fees["line"],
        "fees": fees,
        "charter_sha256": circle.charter_sha256,
        "buffer_seed": money_str(charter.buffer.seed),
        "quorum": charter.quorum,
        "grace_days": charter.grace_days,
        "remedy_menu": list(charter.remedy_menu),
        # Full names ("Judge Payee, Judge Member" rather than "Judge, Judge"); `you` marks the invite's member.
        "members": [{"handle": m.handle, "name": m.name, "you": m.handle == viewer} for m in charter.members],
        "billing_plan": jn.billing_plan_from_charter(charter),
        "kind": circle.kind or "fixture",
        "status": circle.status,
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


def _back(token: str, status: str) -> RedirectResponse:
    return RedirectResponse(f"/join/{token}?status={status}", status_code=303)


async def _notify(circle_id: str, level: str, text: str) -> None:
    try:
        publish = sessions.circle_publisher(circle_id)
        await publish({"type": "toast", "level": level, "text": text})
        await publish({"type": "board"})
    except Exception as e:  # noqa: BLE001 - a missing UI must never fail a member's join
        log.info("join notify failed: %s", e)


# --- organizer: invites and the judge mini-circle ------------------------------------------------------------


@router.get("/api/circles/{circle_id}/invites")
async def invites(circle_id: str, sess: CurrentSession) -> list[dict[str, Any]]:
    if not jn.session_manages(sess.sid, sess.circle_id, circle_id):
        raise HTTPException(status_code=403, detail="circle does not belong to this session")
    return jn.invites_for(circle_id)


@router.post("/api/join/judge-circle")
async def judge_circle(sess: CurrentSession) -> dict[str, Any]:
    """A $5 two-member circle (judge_payee receives round 1, judge_member joins) that uses the real join flow."""
    circle = jn.ensure_judge_circle(sess.sid, sess.run_id)
    charter = state.charter_of(circle)
    token = jn.make_invite(circle.id, jn.JUDGE_MEMBER, ttl=jn.JUDGE_INVITE_TTL)
    members = {m.handle: m for m in state.members_of(circle.id)}
    return {
        "ok": True,
        "circle_id": circle.id,
        "invite": {
            "handle": jn.JUDGE_MEMBER,
            "token": token,
            "url": jn.invite_url(token),
            "path": f"/join/{token}",
            "expires_at": iso(utcnow() + jn.JUDGE_INVITE_TTL),
        },
        "charter": jn.charter_summary(charter),
        "fees": jn.fees_summary(charter),
        "members": [jn.member_card(circle, charter, m) for m in members.values()],
        "paypal_ready": get_settings().paypal_configured,
        "payee_configured": bool(members.get(jn.JUDGE_PAYEE) and members[jn.JUDGE_PAYEE].payout_receiver),
        "notes": [
            "Open the invite on a phone or a private window and log in on PayPal's page with any US sandbox "
            "personal account (developer.paypal.com > Sandbox accounts). Sandbox only: no real money moves.",
            "judge_payee receives round 1's pot; judge_member's turn is round 2.",
        ],
    }


# --- member: card, start, return, cancel, wallet -------------------------------------------------------------


@router.get("/api/join/{token}")
async def join_info(token: str) -> dict[str, Any]:
    inv = _invite(token)
    card = _charter_card(inv.circle_id, viewer=inv.handle)
    m = _member(inv.circle_id, inv.handle)
    circle, charter = _circle_and_charter(inv.circle_id)
    member = jn.member_card(circle, charter, m)
    return {
        "ok": True,
        "circle": card,
        "member": {"handle": m.handle, "name": m.name, "lang": m.lang},
        "vaulted": _vaulted(m),
        "join": member,
        "invite": {"expires_at": iso(inv.expires_at)},
        "paypal_ready": get_settings().paypal_configured,
        "consent": (
            f"You approve {charter.name} to collect ${money_str(charter.amount)} {charter.cadence} for "
            f"{len(charter.members)} rounds from your PayPal account, starting {charter.start_date.isoformat()}. "
            "PayPal shows this schedule and asks you once. Charges follow the frozen charter "
            f"(hash {circle.charter_sha256[:12]}); any change needs a new charter and your fresh consent."
        ),
    }


@router.post("/api/join/{token}/start")
async def join_start(token: str, request: Request) -> dict[str, Any]:
    inv = _invite(token)
    circle, charter = _circle_and_charter(inv.circle_id)
    row = _member(inv.circle_id, inv.handle)
    if circle.status != "active":
        raise HTTPException(status_code=409, detail="This circle is closed; joining is not possible.")
    if _vaulted(row):
        raise HTTPException(
            status_code=409, detail={"status": jn.VAULTED, "message": jn.STATUS_MESSAGES[jn.VAULTED]}
        )
    if (row.join_attempts or 0) >= jn.MAX_SETUP_ATTEMPTS:
        raise HTTPException(
            status_code=429, detail="Too many PayPal attempts for this invite; ask the organizer"
        )
    if not get_settings().paypal_configured:
        raise HTTPException(status_code=503, detail="PayPal sandbox credentials not configured")
    allowed, retry = SETUP_LIMITER.check(f"setup:{client_ip(request.scope)}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many PayPal approvals started from this network; try again later.",
            headers={"Retry-After": str(int(retry) + 1)},
        )
    vault, gw, keys = _paypal_modules()
    attempt = jn.next_setup_attempt(inv.circle_id, inv.handle)
    base = get_settings().public_base_url.rstrip("/")
    try:
        data = await vault.create_setup_token(
            gw,
            request_id=jn.setup_request_id(keys, circle.run_id, inv.circle_id, inv.handle, attempt),
            description=jn.setup_description(charter),
            billing_plan=jn.billing_plan_from_charter(charter),
            return_url=f"{base}/join/{token}/return",
            cancel_url=f"{base}/join/{token}/cancel",
        )
    except Exception as e:  # noqa: BLE001 - PayPal errors become 502, anything else propagates
        if not _is_paypal_error(e):
            raise
        status, message = jn.classify_paypal_error(e)
        jn.update_member(inv.circle_id, inv.handle, join_status=jn.ERROR, join_message=message)
        raise HTTPException(
            status_code=502,
            detail={
                "error": str(getattr(e, "name", "")),
                "issues": list(getattr(e, "issue_codes", [])),
                "status": status,
                "message": message,
            },
        ) from None
    approve = vault.approve_link(data)
    setup_id = data.get("id")
    if not approve or not setup_id:
        message = "PayPal did not return an approval link. Nothing was saved; please try again."
        jn.update_member(inv.circle_id, inv.handle, join_status=jn.ERROR, join_message=message)
        raise HTTPException(
            status_code=502, detail={"error": "NO_APPROVE_LINK", "status": jn.ERROR, "message": message}
        )
    jn.update_member(
        inv.circle_id,
        inv.handle,
        setup_token_id=setup_id,
        join_status=jn.PENDING,
        join_message=jn.STATUS_MESSAGES[jn.PENDING],
    )
    return {"ok": True, "setup_token_id": setup_id, "approve_url": approve, "status": jn.PENDING}


@router.post("/api/join/{token}/setup")
async def join_setup(token: str, request: Request) -> dict[str, Any]:
    return await join_start(token, request)


@router.get("/join/{token}/return")
async def join_return(token: str, approval_token_id: str | None = None) -> RedirectResponse:
    inv = _invite_redirect(token)  # expired is fine: the approval began while the invite was valid
    if isinstance(inv, RedirectResponse):
        return inv  # e.g. Back / reload on the old return URL after vaulting (that link was revoked)
    circle, _charter = _circle_and_charter(inv.circle_id)
    row = _member(inv.circle_id, inv.handle)
    cid, handle = inv.circle_id, inv.handle

    def fail(status: str, message: str) -> RedirectResponse:
        jn.update_member(cid, handle, join_status=status, join_message=message)
        return _back(token, status)

    if row.setup_token_id is None and _vaulted(row):
        # A vaulted member on a link that still verifies: never overwrite the vaulted status with an error.
        return _back(token, jn.VAULTED)
    setup_id = row.setup_token_id
    if not setup_id:
        return fail(
            jn.ERROR, "No PayPal approval is in progress for this invite. Tap Continue to PayPal to start."
        )
    if approval_token_id != setup_id:
        # PayPal's Vault return always appends approval_token_id: a missing or foreign one is not its redirect.
        return fail(jn.ERROR, "This PayPal approval does not belong to this invite. Nothing was saved.")
    vault, gw, keys = _paypal_modules()

    getter = getattr(vault, "get_setup_token", None)
    if getter is not None:
        try:
            st = jn.setup_token_status(await getter(gw, setup_token_id=setup_id))
        except Exception as e:  # noqa: BLE001 - the status read is advisory; creation below decides
            if not _is_paypal_error(e):
                raise
            st = ""
        if st in ("CREATED", "PAYER_ACTION_REQUIRED"):
            return fail(jn.ACTION_REQUIRED, jn.STATUS_MESSAGES[jn.ACTION_REQUIRED])

    attempt = max(row.join_attempts or 1, 1)
    try:
        data = await vault.create_payment_token(
            gw,
            setup_token_id=setup_id,
            request_id=f"{jn.setup_request_id(keys, circle.run_id, cid, handle, attempt)}:pt",
        )
    except Exception as e:  # noqa: BLE001
        if not _is_paypal_error(e):
            raise
        status, message = jn.classify_paypal_error(e)
        log.warning("payment token creation failed for %s: %s", handle, getattr(e, "name", ""))
        if status == jn.ERROR:
            await _notify(
                cid, "warn", f"{handle}: PayPal could not save the approval ({getattr(e, 'name', '')})"
            )
        return fail(status, message)
    vault_id = data.get("id")
    if not vault_id:
        return fail(
            jn.ERROR, "PayPal did not return a saved payment method. Nothing was saved; please try again."
        )
    fields: dict[str, Any] = {
        "vault_id": vault_id,
        "customer_id": (data.get("customer") or {}).get("id"),
        "setup_token_id": None,
        "join_status": jn.VAULTED,
        "join_message": jn.STATUS_MESSAGES[jn.VAULTED],
        "joined_at": utcnow(),
    }
    email = jn.payer_email(data)
    if email and not row.payout_receiver:
        fields["payout_receiver"] = email  # default pot destination: the approving PayPal account
        fields["payout_wallet"] = "PAYPAL"
    jn.update_member(cid, handle, **fields)
    # Revoke every link handed out before joining (a shared invite must not reach the wallet afterwards); the
    # member continues on a fresh link signed with the new nonce.
    jn.rotate_invite_nonce(cid, handle)
    ttl = jn.JUDGE_INVITE_TTL if circle.kind == jn.JUDGE_KIND else jn.INVITE_TTL
    fresh = jn.make_invite(cid, handle, ttl=ttl)
    await _notify(cid, "success", f"{handle} joined and vaulted a payment method")
    return _back(fresh, jn.VAULTED)


@router.get("/join/{token}/cancel")
async def join_cancel(token: str) -> RedirectResponse:
    inv = _invite_redirect(token)
    if isinstance(inv, RedirectResponse):
        return inv
    row = jn.get_member(inv.circle_id, inv.handle)
    if row is not None and not _vaulted(row):
        jn.update_member(
            inv.circle_id,
            inv.handle,
            setup_token_id=None,
            join_status=jn.CANCELLED,
            join_message=jn.STATUS_MESSAGES[jn.CANCELLED],
        )
    return _back(token, jn.CANCELLED)


class WalletBody(BaseModel):
    wallet: Literal["PAYPAL", "VENMO"] = "PAYPAL"
    receiver: str | None = Field(default=None, max_length=254)


def wallet_change_allowed(row: MemberRow, *, now: Any = None) -> bool:
    """A member may set the payout receiver once vaulted: freely while none is on file, otherwise only within
    jn.WALLET_CHANGE_WINDOW of joining (the mandate binds handles, not receivers, so a later change would quietly
    redirect the pot). After that only the organizer can change it."""
    if not _vaulted(row):
        return False
    if not row.payout_receiver:
        return True
    if row.joined_at is None:
        return False
    return (now or utcnow()) - as_utc(row.joined_at) <= jn.WALLET_CHANGE_WINDOW


def _log_wallet_change(circle_id: str, text: str) -> dict[str, Any]:
    """Persist the change in the circle's event log (AgentTrace, kind system) and return the trace event."""
    event = {"type": "trace", "kind": "system", "source": "join", "text": text}
    try:
        circle = state.load_circle(circle_id)
        with session_scope() as db:
            db.add(
                AgentTrace(
                    circle_id=circle_id, run_id=circle.run_id, kind="system", payload=json.dumps(event)
                )
            )
    except Exception as e:  # noqa: BLE001 - logging the change must never fail the member's request
        log.info("wallet change log failed: %s", type(e).__name__)
    return event


@router.post("/api/join/{token}/wallet")
async def join_wallet(token: str, body: WalletBody) -> dict[str, Any]:
    inv = _invite(token)
    row = _member(inv.circle_id, inv.handle)
    if body.wallet == "VENMO":
        raise HTTPException(
            status_code=422,
            detail="Venmo payouts are switched off in this sandbox demo; choose the PayPal wallet.",
        )
    receiver = (body.receiver or "").strip() or row.payout_receiver
    if not receiver:
        raise HTTPException(status_code=422, detail="Enter the PayPal email that should receive your pot.")
    if not _EMAIL_RX.match(receiver):
        raise HTTPException(status_code=422, detail="That does not look like an email address.")
    if not _vaulted(row):
        raise HTTPException(
            status_code=409,
            detail="Join first: approve the schedule on PayPal, then pick your payout wallet.",
        )
    masked = jn.mask_email(receiver)
    if receiver == row.payout_receiver and (row.payout_wallet or "PAYPAL") == "PAYPAL":
        return {"ok": True, "payout_wallet": "PAYPAL", "payout_receiver_masked": masked}  # nothing changes
    if not wallet_change_allowed(row):
        raise HTTPException(status_code=409, detail="ask the organizer to change your payout wallet")
    jn.update_member(inv.circle_id, inv.handle, payout_wallet="PAYPAL", payout_receiver=receiver)
    before = jn.mask_email(row.payout_receiver) or "none"
    text = f"{inv.handle} changed the payout receiver: {before} -> {masked} (PayPal wallet)"
    event = _log_wallet_change(inv.circle_id, text)
    try:
        await sessions.circle_publisher(inv.circle_id)(event)
    except Exception as e:  # noqa: BLE001 - a missing UI must never fail the member's request
        log.info("wallet change trace publish failed: %s", e)
    await _notify(inv.circle_id, "warn" if row.payout_receiver else "info", text)
    return {"ok": True, "payout_wallet": "PAYPAL", "payout_receiver_masked": masked}
