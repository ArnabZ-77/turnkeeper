"""Join flow engine (PLAN.md section 3 step 5): invite tokens, circle creation from a charter, the judge mini-circle,
the charter card (with the fees line) and the PayPal error -> member message mapping.

Invite token: ``{circle_id}.{handle}.{exp}.{sig}`` where ``exp`` is the expiry as base-36 Unix seconds and ``sig``
is the first 24 hex chars of HMAC-SHA256(SESSION_SECRET, "tk-invite:v2|{circle_id}|{handle}|{exp}[|{nonce}]"),
where ``nonce`` is the member's MemberRow.invite_nonce once one was issued (rotated when the member vaults, so
links handed out before joining stop working). Invites carry no PayPal ids and grant only: read the card, start a
setup token, finish the approval, pick a wallet (within WALLET_CHANGE_WINDOW of joining).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlmodel import Session as DbSession
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.domain.billing_plan import billing_plan_from_charter
from app.domain.charter import (
    ALL_REMEDIES,
    Charter,
    canonical_json,
    charter_sha256,
    due_date,
    rounds_total,
)
from app.domain.fees import estimate_fee, money, net_of, wire_amount
from app.engine.common import iso, utcnow
from app.models import CharterApprovalRow, Circle, Contribution, LedgerRow, MemberRow, Round

log = logging.getLogger("turnkeeper.engine.join")

INVITE_TTL = timedelta(days=7)
JUDGE_INVITE_TTL = timedelta(days=1)
WALLET_CHANGE_WINDOW = timedelta(
    minutes=15
)  # a member may change the payout receiver this long after joining
MAX_SETUP_ATTEMPTS = 5  # PayPal setup tokens per member invite before the organizer must step in
PAYOUT_FEE_RESERVE = Decimal("0.25")  # D1 (d): Payouts item fee; same value as app.engine.rounds
JUDGE_KIND = "judge_mini"
COMPILED_KIND = "compiled"
JUDGE_MEMBER = "judge_member"
JUDGE_PAYEE = "judge_payee"
ORGANIZER = "priya (organizer)"
_ALPHABET = string.ascii_lowercase + string.digits

# Member join statuses (MemberRow.join_status)
INVITED, PENDING, VAULTED, CANCELLED, ACTION_REQUIRED, DECLINED, ERROR = (
    "invited",
    "pending_approval",
    "vaulted",
    "cancelled",
    "action_required",
    "declined",
    "error",
)


class JudgeCircleSettings:
    """Optional sandbox values for the judge mini-circle's payee, read from app.config Settings."""

    def __init__(self) -> None:
        from app.config import get_settings

        s = get_settings()
        self.payee_receiver = s.judge_payee_receiver
        self.payee_vault_id = s.judge_payee_vault_id


# ----------------------------------------------------------------------------------------------------------
# Invite tokens
# ----------------------------------------------------------------------------------------------------------


class InviteError(Exception):
    """kind: "invalid" (bad format or signature) | "expired" | "unavailable" (the check could not run)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Invite:
    circle_id: str
    handle: str
    expires_at: datetime
    token: str


def _b36(n: int) -> str:
    digits = string.digits + string.ascii_lowercase
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


def _sig(circle_id: str, handle: str, exp: str, nonce: str | None = None) -> str:
    """HMAC over the invite fields plus the member's current invite nonce (when one was issued): rotating the
    nonce (``rotate_invite_nonce``) revokes every link handed out before."""
    key = get_settings().session_secret.encode("utf-8")
    msg = f"tk-invite:v2|{circle_id}|{handle}|{exp}" + (f"|{nonce}" if nonce else "")
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()[:24]


def _invite_nonce(circle_id: str, handle: str) -> str | None:
    """The member's current invite nonce; None when no row exists or none was issued. A failed lookup (for
    example "database is locked") raises: treating it as "no nonce" would re-accept links revoked on vault."""
    with session_scope() as db:
        row = db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()
        return row.invite_nonce if row is not None else None


def rotate_invite_nonce(circle_id: str, handle: str) -> str:
    """Issue a new invite nonce for the member: links minted before stop verifying."""
    nonce = secrets.token_hex(8)
    update_member(circle_id, handle, invite_nonce=nonce)
    return nonce


def make_invite(
    circle_id: str, handle: str, *, ttl: timedelta = INVITE_TTL, now: datetime | None = None
) -> str:
    now = now or utcnow()
    exp = _b36(int((now + ttl).timestamp()))
    return f"{circle_id}.{handle}.{exp}.{_sig(circle_id, handle, exp, _invite_nonce(circle_id, handle))}"


_TOKEN_RX = re.compile(r"^([a-z0-9]{6})\.([a-z][a-z0-9_]{1,15})\.([0-9a-z]{1,10})\.([0-9a-f]{24})$")


def parse_invite(token: str, *, now: datetime | None = None, allow_expired: bool = False) -> Invite:
    m = _TOKEN_RX.match(token or "")
    if not m:
        raise InviteError("invalid", "This invite link is not valid. Ask the organizer for a new one.")
    cid, handle, exp, sig = m.groups()
    try:
        nonce = _invite_nonce(cid, handle)
    except Exception as e:  # noqa: BLE001 - fail closed: never fall back to a nonce-less signature check
        log.warning("invite nonce lookup failed: %s", type(e).__name__)
        raise InviteError(
            "unavailable", "This invite link could not be checked right now. Please try again in a minute."
        ) from None
    if not hmac.compare_digest(sig, _sig(cid, handle, exp, nonce)):
        raise InviteError("invalid", "This invite link is not valid. Ask the organizer for a new one.")
    expires_at = datetime.fromtimestamp(int(exp, 36), tz=UTC)
    if not allow_expired and (now or utcnow()) >= expires_at:
        raise InviteError("expired", "This invite link has expired. Ask the organizer for a new one.")
    return Invite(cid, handle, expires_at, token)


def invite_url(token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/join/{token}"


# ----------------------------------------------------------------------------------------------------------
# Ownership: the session's demo clone, or circles the session created (judge mini-circle, compiled charters)
# ----------------------------------------------------------------------------------------------------------


def session_manages(sid: str, session_circle_id: str, circle_id: str) -> bool:
    if circle_id == session_circle_id:
        return True
    with session_scope() as db:
        c = db.get(Circle, circle_id)
    return c is not None and bool(c.owner_sid) and hmac.compare_digest(c.owner_sid or "", sid)


# ----------------------------------------------------------------------------------------------------------
# Fees line, charter summary and card
# ----------------------------------------------------------------------------------------------------------


def fees_summary(charter: Charter) -> dict[str, Any]:
    """What each member is told before consenting (PLAN.md: fees shown at consent, not hidden)."""
    n = len(charter.members)
    amount = money(charter.amount)
    fee = estimate_fee(amount)
    net = net_of(amount, fee)
    gross_pot = money(amount * n)
    # Only net_pot is implemented (the compiler maps any other policy to net_pot with a problem), so this is
    # the only fee story a member is ever told.
    pot = money(net * n - PAYOUT_FEE_RESERVE)
    line = (
        f"PayPal's fee (3.49% + $0.49, about ${wire_amount(fee)} on each ${wire_amount(amount)}) comes out of "
        f"each contribution. The pot is paid net of fees: about ${wire_amount(pot)} per round after the "
        f"${wire_amount(PAYOUT_FEE_RESERVE)} payout fee."
    )
    return {
        "fee_policy": charter.fee_policy,
        "fee_estimate": wire_amount(fee),
        "net_per_contribution": wire_amount(net),
        "gross_pot": wire_amount(gross_pot),
        "pot_estimate": wire_amount(pot),
        "payout_fee_reserve": wire_amount(PAYOUT_FEE_RESERVE),
        "line": line,
    }


def charter_summary(charter: Charter) -> dict[str, Any]:
    n = rounds_total(charter)
    return {
        "name": charter.name,
        "amount": wire_amount(charter.amount),
        "currency": charter.currency,
        "cadence": charter.cadence,
        "cycles": n,
        "start_date": charter.start_date.isoformat(),
        "last_due_date": due_date(charter, n).isoformat(),
        "quorum": charter.quorum,
        "grace_days": charter.grace_days,
        "buffer_seed": wire_amount(charter.buffer.seed),
        "remedy_menu": list(charter.remedy_menu),
        "rotation": list(charter.rotation),
        "languages": list(charter.languages),
    }


def freeze_preview(charter: Charter) -> dict[str, Any]:
    """Canonical JSON, hash and the Vault billing_plan each member will see on PayPal."""
    return {
        "charter_sha256": charter_sha256(charter),
        "canonical_json": canonical_json(charter),
        "billing_plan": billing_plan_from_charter(charter),
        "summary": charter_summary(charter),
        "fees": fees_summary(charter),
    }


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}"


def setup_description(charter: Charter) -> str:
    cadence = "monthly" if charter.cadence == "monthly" else "weekly"
    return f"Turnkeeper · {charter.name} · ${wire_amount(charter.amount)} {cadence} × {len(charter.members)}"


def member_card(circle: Circle, charter: Charter, row: MemberRow) -> dict[str, Any]:
    turn = charter.rotation.index(row.handle) + 1 if row.handle in charter.rotation else None
    try:
        rotation = json.loads(circle.rotation_json) if circle.rotation_json else None
        if isinstance(rotation, list) and row.handle in rotation:
            turn = rotation.index(row.handle) + 1
    except ValueError:
        pass
    status = row.join_status or (VAULTED if is_vaulted(row) else INVITED)
    return {
        "handle": row.handle,
        "name": row.name,
        "lang": row.lang,
        "payout_round": turn,
        "payout_date": due_date(charter, turn).isoformat() if turn else None,
        "payout_wallet": row.payout_wallet,
        "payout_receiver_masked": mask_email(row.payout_receiver),
        "join_status": status,
        "join_message": row.join_message or STATUS_MESSAGES.get(status, ""),
    }


def is_vaulted(row: MemberRow) -> bool:
    return bool(row.vault_id) and not row.vault_id.startswith("VAULT_ID_")


STATUS_MESSAGES = {
    INVITED: "Review the circle below, then continue to PayPal to approve the schedule once.",
    PENDING: "Waiting for your approval on PayPal.",
    VAULTED: "You're in. PayPal saved your approval; contributions are collected on each due date.",
    CANCELLED: "You cancelled on PayPal. Nothing was saved and nothing will be charged.",
    ACTION_REQUIRED: "PayPal still needs you to finish approving. Tap Continue to PayPal again.",
    DECLINED: "PayPal declined this payment method. Pick another funding source in PayPal, or ask the organizer.",
    ERROR: "Something went wrong with PayPal. Nothing was saved; please try again.",
}


# ----------------------------------------------------------------------------------------------------------
# PayPal outcome -> member status
# ----------------------------------------------------------------------------------------------------------

DECLINE_CODES = frozenset(
    {
        "INSTRUMENT_DECLINED",
        "PAYER_CANNOT_PAY",
        "PAYMENT_DENIED",
        "TRANSACTION_REFUSED",
        "PAYER_ACCOUNT_RESTRICTED",
        "PAYER_ACCOUNT_LOCKED_OR_CLOSED",
        "COMPLIANCE_VIOLATION",
        "PAYMENT_SOURCE_DECLINED_BY_PROCESSOR",
        "PAYMENT_SOURCE_CANNOT_BE_USED",
    }
)


def classify_paypal_error(err: BaseException) -> tuple[str, str]:
    """(status, message) for a PayPalError raised while creating the payment token."""
    codes = [str(c).upper() for c in (getattr(err, "issue_codes", None) or [])]
    name = str(getattr(err, "name", "") or "").upper()
    every = [*codes, name]
    if any("PAYER_ACTION_REQUIRED" in c or "NOT_APPROVED" in c or "APPROVAL" in c for c in every):
        return ACTION_REQUIRED, STATUS_MESSAGES[ACTION_REQUIRED]
    hit = next((c for c in every if c in DECLINE_CODES or c.endswith("_DECLINED")), None)
    if hit:
        return (
            DECLINED,
            f"PayPal declined this payment method ({hit}). Pick another funding source in PayPal, or ask the organizer.",
        )
    label = codes[0] if codes else (name or "error")
    return ERROR, f"PayPal error ({label}). Nothing was saved; please try again."


def setup_token_status(data: dict[str, Any]) -> str:
    """APPROVED / VAULTED / TOKENIZED mean the payment token can be created; anything else is not approved yet."""
    return str((data or {}).get("status") or "").upper()


def payer_email(payment_token: dict[str, Any]) -> str | None:
    src = (payment_token or {}).get("payment_source") or {}
    pp = src.get("paypal") if isinstance(src, dict) else None
    email = pp.get("email_address") if isinstance(pp, dict) else None
    return str(email) if email else None


# ----------------------------------------------------------------------------------------------------------
# Member rows
# ----------------------------------------------------------------------------------------------------------


def get_member(circle_id: str, handle: str) -> MemberRow | None:
    with session_scope() as db:
        return db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()


def update_member(circle_id: str, handle: str, **fields: Any) -> MemberRow | None:
    with session_scope() as db:
        row = db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        db.add(row)
        return row


def next_setup_attempt(circle_id: str, handle: str) -> int:
    """Bump and return the member's setup attempt (1, 2, ...). Attempt 1 keeps the contract's Request-Id."""
    with session_scope() as db:
        row = db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == handle)
        ).first()
        if row is None:
            raise LookupError(handle)
        row.join_attempts = (row.join_attempts or 0) + 1
        db.add(row)
        return row.join_attempts


def setup_request_id(keys: Any, run_id: str, circle_id: str, handle: str, attempt: int) -> str:
    base = keys.setup_request_id(run_id, circle_id, handle)
    return base if attempt <= 1 else f"{base}:a{attempt}"


# ----------------------------------------------------------------------------------------------------------
# Circles from a charter (judge mini-circle, confirmed compiled charters)
# ----------------------------------------------------------------------------------------------------------


def _new_circle_id(db: DbSession) -> str:
    while True:
        cid = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        if db.get(Circle, cid) is None:
            return cid


def create_circle(
    charter: Charter,
    *,
    run_id: str,
    owner_sid: str,
    kind: str,
    member_overrides: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> Circle:
    """Insert a fresh (no history) circle: members to invite, PENDING rounds and contributions, buffer seed.

    No payout mandate is created: the organizer approves that separately (PLAN.md section 3 step 4)."""
    now = now or utcnow()
    overrides = member_overrides or {}
    with session_scope() as db:
        cid = _new_circle_id(db)
        circle = Circle(
            id=cid,
            run_id=run_id,
            name=charter.name,
            charter_json=canonical_json(charter),
            charter_sha256=charter_sha256(charter),
            fee_policy=charter.fee_policy,
            status="active",
            is_fixture_clone=False,
            virtual_now=now,
            created_at=now,
            flags_json=json.dumps({"ravi_declines": False, "next_payout_unclaimed": False}),
            counters_json=json.dumps(
                {"organizer_messages_typed": 0, "rounds_paid_on_time": 0, "shortfalls_covered": 0}
            ),
            owner_sid=owner_sid,
            kind=kind,
        )
        db.add(circle)
        pos = {h: i for i, h in enumerate(charter.rotation, start=1)}
        for m in charter.members:
            ov = overrides.get(m.handle, {})
            vault_id = ov.get("vault_id") or None
            db.add(
                MemberRow(
                    circle_id=cid,
                    handle=m.handle,
                    name=m.name,
                    lang=m.lang,
                    email=m.email,
                    payday_rule=m.payday_rule,
                    vault_id=vault_id,
                    payout_wallet=m.payout_wallet,
                    payout_receiver=ov.get("payout_receiver") or m.payout_receiver,
                    rotation_pos=pos.get(m.handle, 0),
                    track_record_rounds=m.track_record_rounds,
                    join_status=VAULTED if vault_id else INVITED,
                    joined_at=now if vault_id else None,
                )
            )
        if charter.buffer.seed > 0:
            db.add(
                LedgerRow(
                    circle_id=cid,
                    kind="buffer_seed",
                    amount=wire_amount(charter.buffer.seed),
                    ref="charter:seed",
                    ts=now,
                )
            )
        for n in range(1, rounds_total(charter) + 1):
            due = datetime.combine(due_date(charter, n), time(9, 0), tzinfo=UTC)
            db.add(
                Round(
                    circle_id=cid, n=n, due_at=due, recipient_handle=charter.rotation[n - 1], status="PENDING"
                )
            )
            for m in charter.members:
                db.add(
                    Contribution(
                        circle_id=cid, round_n=n, member_handle=m.handle, status="PENDING", attempt=0
                    )
                )
        db.flush()
    log.info("circle %s created kind=%s members=%d", cid, kind, len(charter.members))
    return circle


def judge_mini_charter(today: date | None = None) -> Charter:
    """$5 monthly, 2 members: judge_payee (has a track record, receives round 1) and judge_member (joins)."""
    today = today or utcnow().date()
    start = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return Charter.model_validate(
        {
            "name": "Judge Mini Circle",
            "amount": "5.00",
            "cadence": "monthly",
            "start_date": start,
            "members": [
                {"handle": JUDGE_PAYEE, "name": "Judge Payee", "track_record_rounds": 1},
                {"handle": JUDGE_MEMBER, "name": "Judge Member", "track_record_rounds": 0},
            ],
            "rotation": [JUDGE_PAYEE, JUDGE_MEMBER],
            "fee_policy": "net_pot",
            "buffer": {"seed": "0.00"},
            # No buffer, so no buffer advance: "If short" must not promise that the organizer's buffer covers you.
            "remedy_menu": [r for r in ALL_REMEDIES if r != "buffer_advance"],
            "grace_days": 3,
            "quorum": "2/2",
            "languages": ["en"],
        }
    )


def ensure_judge_circle(sid: str, run_id: str) -> Circle:
    """The session's judge mini-circle for its current run (created once per run)."""
    with session_scope() as db:
        existing = db.exec(
            select(Circle).where(
                Circle.owner_sid == sid,
                Circle.kind == JUDGE_KIND,
                Circle.run_id == run_id,
                Circle.status == "active",
            )
        ).first()
    if existing is not None:
        return existing
    js = JudgeCircleSettings()
    payee: dict[str, Any] = {}
    if js.payee_receiver:
        payee["payout_receiver"] = js.payee_receiver
    if js.payee_vault_id:
        payee["vault_id"] = js.payee_vault_id
    return create_circle(
        judge_mini_charter(),
        run_id=run_id,
        owner_sid=sid,
        kind=JUDGE_KIND,
        member_overrides={JUDGE_PAYEE: payee},
    )


def circle_for_draft(draft_id: str) -> str | None:
    """The circle an earlier confirmation of this draft created or approved (confirm is idempotent)."""
    with session_scope() as db:
        row = db.exec(select(CharterApprovalRow).where(CharterApprovalRow.draft_id == draft_id)).first()
        return row.circle_id if row else None


def record_approval(
    circle_id: str, sha: str, *, draft_id: str | None, user_agent: str = ""
) -> dict[str, Any]:
    now = utcnow()
    with session_scope() as db:
        row = CharterApprovalRow(
            circle_id=circle_id,
            charter_sha256=sha,
            draft_id=draft_id,
            approved_by=ORGANIZER,
            user_agent=(user_agent or "")[:200],
            approved_at=now,
        )
        db.add(row)
    return {
        "circle_id": circle_id,
        "charter_sha256": sha,
        "approved_by": ORGANIZER,
        "approved_at": iso(now),
        "draft_id": draft_id,
    }


def latest_approval(circle_id: str) -> dict[str, Any] | None:
    with session_scope() as db:
        row = db.exec(
            select(CharterApprovalRow)
            .where(CharterApprovalRow.circle_id == circle_id)
            .order_by(CharterApprovalRow.id.desc())
        ).first()
    if row is None:
        return None
    return {
        "circle_id": row.circle_id,
        "charter_sha256": row.charter_sha256,
        "approved_by": row.approved_by,
        "approved_at": iso(row.approved_at),
        "draft_id": row.draft_id,
    }


def invites_for(circle_id: str, *, ttl: timedelta = INVITE_TTL) -> list[dict[str, Any]]:
    from app.engine import state

    out = []
    now = utcnow()
    for m in state.members_of(circle_id):
        token = make_invite(circle_id, m.handle, ttl=ttl, now=now)
        out.append(
            {
                "handle": m.handle,
                "name": m.name,
                "token": token,
                "url": invite_url(token),
                "vaulted": is_vaulted(m),
                "join_status": m.join_status or (VAULTED if is_vaulted(m) else INVITED),
                "expires_at": iso(now + ttl),
            }
        )
    return out
