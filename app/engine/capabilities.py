"""Member capabilities in use: mint for a member, show the request, answer it once, read the repayment.

The token logic is pure (``app.domain.capability``). This module adds what needs state:

- a capability works only on a demo circle (``is_fixture_clone``) with member mandates on and not killed;
- an answer token is for the remedy set it was minted on; a newer or edited set is ``SET_SUPERSEDED``;
- one answer per token: the nonce is the primary key of ``capability_uses`` (survives a restart);
- the member's agent rule is the floor: an outside "yes" that ``member_agent.decide`` would refuse is recorded as
  "no" (and says so). An "ask" leaves the human's answer as given;
- the assistant's rationale is kept for the audit row only (cut to 280 characters, then HTML-escaped, so an
  entity is never cut in half); it never reaches a prompt and no page renders it today;
- a read token (the passbook link) sees the open proposal without the short member's name and without other
  members' handles or amounts, unless the shortfall is that member's own: the passbook's dignity rule holds on the
  MCP too. When the short member is named, it is by first name only, as on the rest of the public surface.

No money moves here. ``repayment`` reads the member's PayPal invoice (toolkit ``get_invoice`` when the sandbox is
live) and sends PayPal's own reminder (toolkit ``send_invoice_reminder``) once, when the invoice is still unpaid
after the date the member promised. Sending is a write, so it needs an answer-scoped token; a read token (the
passbook link) only reports ``reminder: "due"``. "Once" is claimed in the database before the send (a conditional
UPDATE), so two concurrent calls cannot both send.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.agent.handles import first_name
from app.config import get_settings
from app.db import session_scope
from app.domain import capability as capm
from app.domain.labels import names_of
from app.engine import member_agent, member_mandates, recovery, state
from app.engine.clock import VirtualClock
from app.engine.common import utcnow
from app.engine.events import Publish
from app.models import CapabilityUse, Remedy

log = logging.getLogger("turnkeeper.engine.capabilities")

SOURCE = "member_mcp"  # how an answer given through a capability is recorded (votes.source)
RATIONALE_MAX = 280


def _now() -> int:
    return int(utcnow().timestamp())


def _secret() -> str:
    return get_settings().mandate_hmac_secret


def _circle_open(circle_id: str) -> None:
    try:
        circle = state.load_circle(circle_id)
    except LookupError:
        raise capm.CapabilityError(capm.NOT_A_DEMO_CIRCLE, f"circle {circle_id!r} not found") from None
    if not circle.is_fixture_clone:
        raise capm.CapabilityError(capm.NOT_A_DEMO_CIRCLE, f"circle {circle_id!r} is not a demo circle")
    if member_mandates.killed() or not member_mandates.enabled(circle_id):
        raise capm.CapabilityError(capm.CAPABILITY_SCOPE, "member agents are off for this circle")


def _member_of(circle_id: str, member: str) -> None:
    if member not in {m.handle for m in state.load_charter(circle_id).members}:
        raise capm.CapabilityError(capm.CAPABILITY_SCOPE, f"{member!r} is not a member of this circle")


def mint_answer(circle_id: str, member: str) -> dict[str, Any]:
    """A 15-minute token that answers this member's one pending item (consent first, then vote) on the open set."""
    _circle_open(circle_id)
    _member_of(circle_id, member)
    row = recovery.latest_set(circle_id)
    if row is None or row.status != "voting":
        raise capm.CapabilityError(capm.SET_SUPERSEDED, "there is no proposal open for answers")
    pending = [
        k for k, m in member_agent._pending_items(circle_id, row.id, ("consent", "vote")) if m == member
    ]  # noqa: SLF001
    if not pending:
        raise capm.CapabilityError(
            capm.CAPABILITY_USED, f"{member} has nothing left to answer on this proposal"
        )
    token, cap = capm.mint(
        circle_id=circle_id,
        member=member,
        scope="answer",
        now=_now(),
        secret=_secret(),
        ttl=capm.ANSWER_TTL,
        set_id=row.id,
        set_sha=capm.set_digest(row.json),
        item=pending[0],
    )
    return {"ok": True, "token": token, "member": member, "item": cap.item, "expires_at": cap.exp}


def mint_read(circle_id: str, member: str, *, ttl: int = capm.MAX_TTL) -> dict[str, Any]:
    """A read-only token (the member's passbook and repayment), up to a day."""
    try:
        circle = state.load_circle(circle_id)
    except LookupError:
        raise capm.CapabilityError(capm.NOT_A_DEMO_CIRCLE, f"circle {circle_id!r} not found") from None
    if not circle.is_fixture_clone:
        raise capm.CapabilityError(capm.NOT_A_DEMO_CIRCLE, f"circle {circle_id!r} is not a demo circle")
    _member_of(circle_id, member)
    token, cap = capm.mint(
        circle_id=circle_id, member=member, scope="read", now=_now(), secret=_secret(), ttl=ttl
    )
    return {"ok": True, "token": token, "member": member, "expires_at": cap.exp}


def open_token(token: str, member: str | None = None) -> capm.Capability:
    cap = capm.verify(token, secret=_secret(), now=_now())
    capm.require_member(cap, member)
    return cap


def _current_set(cap: capm.Capability):  # noqa: ANN202 - RemedySetRow
    row = recovery.latest_set(cap.circle_id)
    if row is None or row.id != cap.set_id or capm.set_digest(row.json) != cap.set_sha:
        raise capm.CapabilityError(capm.SET_SUPERSEDED, "the proposal changed since this token was issued")
    if row.status != "voting":
        raise capm.CapabilityError(
            capm.SET_SUPERSEDED, f"the proposal is {row.status}, no longer open for answers"
        )
    return row


def request_view(token: str, member: str | None = None) -> dict[str, Any]:
    """What the member is being asked (or, for a read token, the open proposal), in plain words."""
    cap = open_token(token, member)
    if cap.scope == "answer":
        _circle_open(cap.circle_id)
    row = _current_set(cap) if cap.scope == "answer" else recovery.latest_set(cap.circle_id)
    out: dict[str, Any] = {"ok": True, "member": cap.member, "scope": cap.scope, "expires_at": cap.exp}
    if row is None:
        return {**out, "request": None}
    rs = recovery.domain_set(row)
    charter = state.load_charter(cap.circle_id)
    names = names_of(charter.members)
    # Dignity rule (as on the passbook): a read token names nobody unless the shortfall is the viewer's own.
    # An answer token belongs to a member asked to vote or consent on this proposal, who sees what they decide.
    named = cap.scope == "answer" or cap.member == rs.member
    remedies = []
    for r in rs.remedies:
        line: dict[str, Any] = {"type": r.type}
        mine = cap.member in (r.member, r.params.swap_with)
        if named:
            line["member"] = r.member
        else:
            line["mine"] = mine
        if r.params.amount is not None and (named or mine):
            line["amount"] = str(r.params.amount)
        if r.params.swap_with and named:
            line["swap_with"] = r.params.swap_with
        if r.params.due_date:
            line["due_date"] = r.params.due_date.isoformat()
        remedies.append(line)
    return {
        **out,
        "request": {
            "item": cap.item,
            "choices": list(cap.choices),
            "round_n": rs.round_n,
            # first name only, as everywhere on the public surface (mask_pii shortens only keys named "name")
            "short_member": first_name(names.get(rs.member, rs.member)) if named else None,
            "remedies": remedies,
            "status": row.status,
            "charter_sha256": rs.charter_sha256,
        },
    }


async def answer(
    token: str,
    choice: str,
    rationale: str = "",
    *,
    member: str | None = None,
    publish: Publish = recovery._noop,  # noqa: SLF001
) -> dict[str, Any]:
    cap = open_token(token, member)
    capm.require_answer(cap, choice)
    _circle_open(cap.circle_id)
    row = _current_set(cap)
    pending = [
        k for k, m in member_agent._pending_items(cap.circle_id, row.id, (cap.item,)) if m == cap.member
    ]  # noqa: SLF001
    if not pending:
        raise capm.CapabilityError(capm.CAPABILITY_USED, f"{cap.member} already answered this {cap.item}")

    rs = recovery.domain_set(row)
    request = member_agent.AgentRequest(cap.item, cap.member, rs)
    mandate = member_mandates.load_mandate(cap.circle_id, cap.member)
    floor = member_agent.decide(
        request,
        mandate,
        state.load_charter(cap.circle_id),
        recovery.recovery_state(cap.circle_id, rs.round_n),
    )
    recorded = "no" if (choice == "yes" and floor.choice == "no") else choice
    clean = html.escape((rationale or "").strip()[:RATIONALE_MAX])

    try:  # spend the token first: a second use fails on the primary key, also after a restart
        with session_scope() as db:
            db.add(
                CapabilityUse(
                    nonce=cap.nonce,
                    circle_id=cap.circle_id,
                    member=cap.member,
                    set_id=row.id,
                    item=cap.item or "",
                    choice=choice,
                    recorded_choice=recorded,
                    rationale=clean,
                )
            )
    except IntegrityError:
        raise capm.CapabilityError(capm.CAPABILITY_USED, "this token was already used") from None

    try:
        if cap.item == "vote":
            status = await recovery.cast_vote(
                cap.circle_id, row.id, cap.member, recorded, source=SOURCE, publish=publish
            )
        else:
            status = await recovery.record_consent(
                cap.circle_id, row.id, cap.member, recorded, source=SOURCE, publish=publish
            )
    except recovery.RecoveryError as e:
        # nothing was recorded (the vote closed, or there is no such item): un-spend the token, so the audit
        # table never claims an answer that did not land, and name the refusal
        with session_scope() as db:
            use = db.get(CapabilityUse, cap.nonce)
            if use is not None:
                db.delete(use)
        raise capm.CapabilityError(capm.SET_SUPERSEDED, f"the answer was not recorded: {e}") from None
    note = (
        f"your assistant said yes, but your own mandate refuses this ({floor.reason}); recorded as no"
        if recorded != choice
        else None
    )
    await publish(
        {
            "type": "trace",
            "kind": "system",
            "source": "member_mcp",
            "text": f"{cap.member}'s own assistant answered {cap.item}: {recorded}"
            + (" (mandate floor)" if note else ""),
        }
    )
    await publish({"type": "board"})
    return {
        "ok": True,
        "member": cap.member,
        "item": cap.item,
        "asked": choice,
        "recorded": recorded,
        "floor": {"choice": floor.choice, "rule": floor.rule, "reason": floor.reason},
        "note": note,
        "votes": status.get("votes"),
        "consents": status.get("consents"),
    }


REMINDER_MARK = " · reminder sent"


def _claim_reminder(remedy_row_id: int | None) -> str | None:
    """Mark the invoice reminded in one conditional UPDATE. Returns the label from before the mark when this call
    won the claim (it may send), or None when the mark is already there (another call sent, or is sending)."""
    with session_scope() as db:
        live = db.get(Remedy, remedy_row_id)
        if live is None:
            return None
        before = live.label
        res = db.connection().execute(
            update(Remedy)
            .where(Remedy.id == remedy_row_id)
            .where(or_(Remedy.label.is_(None), ~Remedy.label.contains(REMINDER_MARK)))  # type: ignore[union-attr]
            .values(label=(before or "Invoice sent") + REMINDER_MARK)
        )
        return (before or "") if res.rowcount == 1 else None


def _unclaim_reminder(remedy_row_id: int | None, before: str) -> None:
    """The send failed: take the mark back so a later call can try again."""
    with session_scope() as db:
        live = db.get(Remedy, remedy_row_id)
        if live is not None and REMINDER_MARK in (live.label or ""):
            live.label = before or None
            db.add(live)


def _invoice_row(circle_id: str, member: str):  # noqa: ANN202 - Remedy row or None
    row = recovery.current_invoice(circle_id)
    if row is None:
        return None
    remedy = recovery.rd.Remedy.model_validate_json(row.json)
    return row if remedy.member == member else None


async def repayment(token: str, member: str | None = None) -> dict[str, Any]:
    """The member's repayment invoice: status (refreshed through the toolkit when live) and the payer link."""
    cap = open_token(token, member)
    row = _invoice_row(cap.circle_id, cap.member)
    if row is None:
        return {"ok": True, "member": cap.member, "invoice": None}
    remedy = recovery.rd.Remedy.model_validate_json(row.json)
    status = row.invoice_status
    source = "ledger"
    reminder: str | None = None
    live = bool(row.invoice_id) and not row.invoice_simulated and get_settings().paypal_configured
    if live:
        try:
            tk = recovery._toolkit()  # noqa: SLF001
            got = await tk.run("get_invoice", {"invoice_id": row.invoice_id})
            raw = got.get("raw") if isinstance(got.get("raw"), dict) else got
            status = str(raw.get("status") or status)
            source = "toolkit get_invoice"
            commitment = recovery.latest_commitment(cap.circle_id, cap.member)
            due = commitment.date if commitment else remedy.params.due_date
            today = VirtualClock(state.load_circle(cap.circle_id)).today()
            if status not in ("PAID", "MARKED_AS_PAID") and due and today > due and cap.scope != "answer":
                reminder = (
                    "already sent" if REMINDER_MARK in (row.label or "") else "due"
                )  # a read token never writes
            elif status not in ("PAID", "MARKED_AS_PAID") and due and today > due:
                before = _claim_reminder(row.id)
                if before is None:
                    reminder = "already sent"
                else:
                    try:
                        await tk.run("send_invoice_reminder", {"invoice_id": row.invoice_id})
                    except Exception:
                        _unclaim_reminder(row.id, before)
                        raise
                    reminder = "sent"
        except Exception as e:  # noqa: BLE001 - the stored status is still a true answer
            log.info("repayment refresh failed for %s: %s", row.invoice_id, type(e).__name__)
    return {
        "ok": True,
        "member": cap.member,
        "invoice": {
            "invoice_id": row.invoice_id,
            "status": status,
            "status_source": source,
            "amount": str(remedy.params.amount) if remedy.params.amount else None,
            "due_date": remedy.params.due_date.isoformat() if remedy.params.due_date else None,
            "qr_url": f"/api/circles/{cap.circle_id}/recovery/qr" if row.invoice_qr else None,
            "simulated": bool(row.invoice_simulated),
            "reminder": reminder,
        },
    }
