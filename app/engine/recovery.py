"""Recovery path: commitment -> remedy proposal -> consents + vote + organizer approval -> execution -> repayment.

PLAN.md section 3 steps 9 and 12, section 4 rows 8, 9 and 14. Every money step is policy-gated
(``app.domain.policy.PolicyEngine``); proposals are checked by ``app.domain.remedies.validate_remedy_set``.

- ``propose`` stores a RemedySet (status ``proposed`` or ``invalid``); ``open_vote`` creates one pending vote per
  member, one consent per affected member (the short member and each swap partner) and the organizer's
  approval; ``approve`` needs quorum + consents, then ``execute_remedies`` runs the set:
  ``buffer_advance`` (ledger + contribution COVERED "Covered by buffer"), ``turn_swap`` (rotation override on the
  Circle, future Round recipients, re-signed mandate, ``schedule_changed``), ``repay_by_invoice`` (Agent Toolkit
  create_invoice -> send_invoice -> generate_invoice_qr_code), ``retry_on_date`` (scheduled only).
- ``on_invoice_paid`` (INVOICING.INVOICE.PAID webhook, or the Judge "mark_invoice_paid" simulation) records
  ``advance_repaid`` and relabels the contribution "Round N: covered, not missed".
- ``run_rules_recovery`` is the deterministic sequence used when no model is available (rules mode).

PayPal is reached only through ``_toolkit()`` / ``_keys()`` (monkeypatched in tests). Results start with "ok".
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.domain import labels as lb
from app.domain import remedies as rd
from app.domain.charter import charter_sha256, quorum_tuple
from app.domain.fees import estimate_fee, net_of
from app.domain.policy import PolicyDenied, PolicyEngine
from app.engine import inbox, state
from app.engine.clock import VirtualClock
from app.engine.common import as_utc, iso, money, utcnow
from app.engine.events import Publish
from app.models import (
    Circle,
    CommitmentRow,
    Contribution,
    LedgerRow,
    MandateRow,
    MemberRow,
    Remedy,
    RemedySetRow,
    Round,
    Vote,
)

log = logging.getLogger("turnkeeper.engine.recovery")

ORGANIZER = "priya"  # the organizer's handle for the one-tap approval (not a charter member)
ACTIVE = ("proposed", "voting", "approved")
VOTE_WINDOW = timedelta(hours=48)  # virtual hours (PLAN.md section 5 "Votes")
COVERED_LABEL = "Covered by buffer"
EXEC_ORDER = {"buffer_advance": 0, "turn_swap": 1, "repay_by_invoice": 2, "retry_on_date": 3}
SIMULATED_INVOICE_PREFIX = "SIM-INV-"
COMMITMENT_HORIZON_DAYS = 60


async def _noop(event: dict) -> None:
    return None


_LOCKS: dict[str, asyncio.Lock] = {}


def _circle_lock(circle_id: str) -> asyncio.Lock:
    """One lock per circle for approve/execute (in-process; the app runs a single worker)."""
    lock = _LOCKS.get(circle_id)
    if lock is None:
        lock = _LOCKS.setdefault(circle_id, asyncio.Lock())
    return lock


# --- accessors (monkeypatched in tests) ---------------------------------------------------------


def _toolkit() -> Any:
    """The shared ToolkitAdapter (same instance the round engine uses for get_order_details)."""
    return importlib.import_module("app.engine.rounds")._toolkit()


def _keys() -> Any:
    return importlib.import_module("app.paypal.keys")


def _rounds() -> Any:
    return importlib.import_module("app.engine.rounds")


class RecoveryError(Exception):
    """A request that cannot be served in the current recovery state (routes map it to 409)."""


# --- small helpers ------------------------------------------------------------------------------


def covered_not_missed(round_n: int) -> str:
    return f"Round {round_n}: covered, not missed"


async def _trace(publish: Publish, text: str, kind: str = "system", **extra: Any) -> None:
    await publish({"type": "trace", "kind": kind, "text": text, "source": "recovery", **extra})


async def _toast(publish: Publish, level: str, text: str) -> None:
    await publish({"type": "toast", "level": level, "text": text})


def _member_rows(circle_id: str) -> dict[str, MemberRow]:
    return {m.handle: m for m in state.members_of(circle_id)}


def _name(circle_id: str, handle: str) -> str:
    """A member's full name for toasts ("Ravi Sharma", not "ravi"); the handle when the lookup fails."""
    try:
        return lb.display_name(handle, lb.names_of(state.members_of(circle_id)))
    except Exception:  # noqa: BLE001 - wording must never fail a money step
        return lb.display_name(handle)


def _today(circle: Circle) -> date:
    return VirtualClock(circle).today()


def _remedy_rows(set_id: str) -> list[Remedy]:
    with session_scope() as db:
        rows = list(db.exec(select(Remedy).where(Remedy.set_id == set_id).order_by(Remedy.id)).all())
    return rows


def _vote_rows(set_id: str, kind: str | None = None) -> list[Vote]:
    with session_scope() as db:
        q = select(Vote).where(Vote.set_id == set_id)
        if kind:
            q = q.where(Vote.kind == kind)
        return list(db.exec(q.order_by(Vote.id)).all())


def get_set(circle_id: str, set_id: str) -> RemedySetRow:
    with session_scope() as db:
        row = db.get(RemedySetRow, set_id)
    if row is None or row.circle_id != circle_id:
        raise LookupError(f"remedy set {set_id} not found")
    return row


def latest_set(circle_id: str, *, include_superseded: bool = False) -> RemedySetRow | None:
    with session_scope() as db:
        q = select(RemedySetRow).where(RemedySetRow.circle_id == circle_id)
        if not include_superseded:
            q = q.where(RemedySetRow.status != "superseded")
        return db.exec(q.order_by(RemedySetRow.created_at.desc())).first()  # type: ignore[attr-defined]


# The model's handle for a remedy set: "rs:<member>:r<round>". Stable across sessions, so a recorded run replays
# in any session (raw "rs_xxxxxxxx" ids are random per proposal).
LOGICAL_SET_RE = re.compile(r"^rs:([a-z][a-z0-9_]{1,15}):r(\d{1,3})$")


def logical_set_id(member: str, round_n: int) -> str:
    return f"rs:{member}:r{int(round_n)}"


def resolve_set_id(circle_id: str, ref: str) -> str:
    """Raw set id for a logical handle (latest non-superseded set for that member and round); raw ids pass."""
    m = LOGICAL_SET_RE.match(ref or "")
    if not m:
        return ref
    member, round_n = m.group(1), int(m.group(2))
    with session_scope() as db:
        row = db.exec(
            select(RemedySetRow)
            .where(
                RemedySetRow.circle_id == circle_id,
                RemedySetRow.member == member,
                RemedySetRow.round_n == round_n,
                RemedySetRow.status != "superseded",
            )
            .order_by(RemedySetRow.created_at.desc())  # type: ignore[attr-defined]
        ).first()
    if row is None:
        raise LookupError(f"no remedy set for {member} in round {round_n}; call propose_remedies first")
    return row.id


def domain_set(row: RemedySetRow) -> rd.RemedySet:
    return rd.RemedySet.model_validate_json(row.json)


def _executed_swaps(circle_id: str) -> set[str]:
    done: set[str] = set()
    with session_scope() as db:
        rows = db.exec(
            select(Remedy).where(
                Remedy.circle_id == circle_id, Remedy.type == "turn_swap", Remedy.status == "executed"
            )
        ).all()
        for r in rows:
            rem = rd.Remedy.model_validate_json(r.json)
            done.add(rem.member)
            if rem.params.swap_with:
                done.add(rem.params.swap_with)
    return done


def recovery_state(circle_id: str, round_n: int) -> Any:
    """CircleState for the policy engine, with the swaps already executed in this circle."""
    cs = state.circle_state(circle_id, round_n)
    return cs.model_copy(update={"swaps_done": _executed_swaps(circle_id)})


def find_shortfall(circle_id: str) -> tuple[int, str] | None:
    """(round_n, member) of the first unpaid round with a DECLINED contribution (charter order)."""
    charter = state.load_charter(circle_id)
    order = [m.handle for m in charter.members]
    with session_scope() as db:
        rounds = list(db.exec(select(Round).where(Round.circle_id == circle_id).order_by(Round.n)).all())
    for rnd in rounds:
        if rnd.status in ("PAID", "VOID"):
            continue
        declined = {
            c.member_handle for c in state.round_contributions(circle_id, rnd.n) if c.status == "DECLINED"
        }
        for handle in order:
            if handle in declined:
                return rnd.n, handle
    return None


def shortfall_problem(circle_id: str, round_n: int, member: str) -> str | None:
    """None when (round_n, member) is a real shortfall: the contribution is DECLINED and the round is still
    open (not PAID or VOID). Otherwise why a remedy cannot target it."""
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        return f"round {round_n} not found"
    if rnd.status in ("PAID", "VOID"):
        return f"round {round_n} is {rnd.status}; there is nothing to remedy"
    contrib = next(
        (c for c in state.round_contributions(circle_id, round_n) if c.member_handle == member), None
    )
    status = contrib.status if contrib is not None else None
    if status != "DECLINED":
        return (
            f"{member} is {status or 'not in'} round {round_n}; only a declined contribution can be remedied"
        )
    return None


# --- commitments --------------------------------------------------------------------------------


def record_commitment(
    circle_id: str,
    member: str,
    amount: Any,
    when: Any,
    *,
    source: str = "rules",
    message_id: int | None = None,
    round_n: int | None = None,
) -> dict[str, Any]:
    """Validate and store a commitment. Amount is capped at one contribution; the date is within 60 days."""
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    if member not in {m.handle for m in charter.members}:
        return {"ok": False, "error": "INVALID_INPUT", "message": f"unknown member {member!r}"}
    try:
        amt = money(amount)
    except Exception:  # noqa: BLE001 - any parse failure is an input problem
        return {"ok": False, "error": "INVALID_INPUT", "message": f"amount {amount!r} is not a money value"}
    if amt <= 0 or amt > money(charter.amount):
        return {
            "ok": False,
            "error": "INVALID_INPUT",
            "message": f"amount {amt} must be positive and at most one contribution ({charter.amount})",
        }
    try:
        day = when if isinstance(when, date) else date.fromisoformat(str(when)[:10])
    except ValueError:
        return {"ok": False, "error": "INVALID_INPUT", "message": f"date {when!r} is not YYYY-MM-DD"}
    today = _today(circle)
    if day < today or day > today + timedelta(days=COMMITMENT_HORIZON_DAYS):
        return {
            "ok": False,
            "error": "INVALID_INPUT",
            "message": f"date {day.isoformat()} must be between {today.isoformat()} and {COMMITMENT_HORIZON_DAYS} days ahead",
        }
    with session_scope() as db:
        row = CommitmentRow(
            circle_id=circle_id,
            member=member,
            amount=str(amt),
            date=day.isoformat(),
            source=source,
            message_id=message_id,
            round_n=round_n,
        )
        db.add(row)
        db.flush()
        cid = row.id
    commitment = {"id": cid, "member": member, "amount": str(amt), "date": day.isoformat(), "source": source}
    return {"ok": True, "commitment": commitment}


def latest_commitment(circle_id: str, member: str) -> rd.Commitment | None:
    with session_scope() as db:
        row = db.exec(
            select(CommitmentRow)
            .where(CommitmentRow.circle_id == circle_id, CommitmentRow.member == member)
            .order_by(CommitmentRow.id.desc())  # type: ignore[union-attr]
        ).first()
    if row is None:
        return None
    return rd.Commitment(
        member=row.member, amount=money(row.amount), date=date.fromisoformat(row.date), source=row.source
    )  # type: ignore[arg-type]


def commitments_of(circle_id: str, member: str) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.exec(
            select(CommitmentRow)
            .where(CommitmentRow.circle_id == circle_id, CommitmentRow.member == member)
            .order_by(CommitmentRow.id)
        ).all()
    return [{"amount": r.amount, "date": r.date, "source": r.source, "round_n": r.round_n} for r in rows]


# --- proposal -----------------------------------------------------------------------------------


def _build_set(
    charter: Any, round_n: int, member: str, remedies: list[dict[str, Any]], source: str
) -> rd.RemedySet:
    sha = charter_sha256(charter)
    items: list[rd.Remedy] = []
    for raw in remedies:
        params = rd.RemedyParams(
            swap_with=raw.get("swap_with") or None,
            amount=raw.get("amount") or None,
            due_date=raw.get("due_date") or None,
        )
        items.append(
            rd.Remedy(
                type=raw["type"],
                member=raw.get("member") or member,
                round_n=int(raw.get("round_n") or round_n),
                params=params,
                reason=str(raw.get("reason") or "")[:500],
                charter_sha256=str(raw.get("charter_sha256") or sha),
            )
        )
    return rd.RemedySet(member=member, round_n=round_n, remedies=items, charter_sha256=sha, source=source)  # type: ignore[arg-type]


def _remedy_view(r: rd.Remedy, row: Remedy | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "type": r.type,
        "member": r.member,
        "round_n": r.round_n,
        "params": {
            "swap_with": r.params.swap_with,
            "amount": str(r.params.amount) if r.params.amount is not None else None,
            "due_date": r.params.due_date.isoformat() if r.params.due_date else None,
        },
        "reason": r.reason,
        "status": row.status if row else "proposed",
        "label": row.label if row else None,
    }


async def propose(
    circle_id: str,
    round_n: int | None,
    member: str | None,
    remedies: list[dict[str, Any]] | None = None,
    *,
    source: str = "rules",
    publish: Publish = _noop,
) -> dict[str, Any]:
    """Validate and store a remedy set. Empty ``remedies`` = the charter-legal default proposal."""
    if round_n is None or member is None:
        found = find_shortfall(circle_id)
        if found is None:
            return {"ok": False, "error": "NO_SHORTFALL", "message": "no declined contribution to remedy"}
        round_n = round_n or found[0]
        member = member or found[1]
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    if member not in {m.handle for m in charter.members}:
        return {"ok": False, "error": "INVALID_INPUT", "message": f"unknown member {member!r}"}
    if (why := shortfall_problem(circle_id, round_n, member)) is not None:
        return {"ok": False, "error": "NO_SHORTFALL", "message": why}
    cs = recovery_state(circle_id, round_n)
    commitment = latest_commitment(circle_id, member)
    if remedies:
        try:
            rs = _build_set(charter, round_n, member, remedies, source)
        except Exception as e:  # noqa: BLE001 - schema problems go back to the proposer
            return {"ok": False, "error": "INVALID_INPUT", "message": f"bad remedy: {e}"[:400]}
    else:
        rs = rd.propose_default_remedies(charter, cs, member, commitment)
        if source != "rules":
            rs = rs.model_copy(update={"source": source})
    problems = rd.validate_remedy_set(charter, cs, rs)
    status = "invalid" if problems else "proposed"
    with session_scope() as db:
        for old in db.exec(
            select(RemedySetRow).where(
                RemedySetRow.circle_id == circle_id,
                RemedySetRow.status.in_(ACTIVE),  # type: ignore[attr-defined]
            )
        ).all():
            old.status = "superseded"
            db.add(old)
        db.add(
            RemedySetRow(
                id=rs.id,
                circle_id=circle_id,
                member=member,
                round_n=round_n,
                status=status,
                source=rs.source,
                charter_sha256=rs.charter_sha256,
                json=rs.model_dump_json(),
                problems_json=json.dumps(problems),
                commitment_json=commitment.model_dump_json() if commitment else None,
            )
        )
        for r in rs.remedies:
            db.add(
                Remedy(
                    circle_id=circle_id,
                    json=r.model_dump_json(),
                    remedy_id=r.id,
                    set_id=rs.id,
                    type=r.type,
                    member=r.member,
                    round_n=r.round_n,
                    status="proposed",
                )
            )
    kinds = ", ".join(r.type for r in rs.remedies) or "none"
    who = _name(circle_id, member)
    nice = lb.remedy_list(r.type for r in rs.remedies)
    if problems:
        await _toast(publish, "warn", f"Remedy proposal for {who} rejected by the charter check")
        await _trace(
            publish, f"Proposal {rs.id} ({kinds}) is not charter-legal: " + "; ".join(problems)[:600]
        )
    else:
        await _toast(publish, "info", f"Remedies proposed for {who}: {nice} (charter OK)")
        await _trace(
            publish, f"Proposal {rs.id} for {member}, round {round_n}: {kinds}. Charter check passed."
        )
    await publish({"type": "board"})
    return {
        "ok": not problems,
        "set_id": rs.id,
        "status": status,
        "member": member,
        "round_n": round_n,
        "remedy_ids": [r.id for r in rs.remedies],
        "remedies": [_remedy_view(r) for r in rs.remedies],
        "problems": problems,
        "commitment": commitment.model_dump(mode="json") if commitment else None,
        **({"denied": "; ".join(problems)} if problems else {}),
    }


# --- votes, consents, approval ------------------------------------------------------------------


async def open_vote(circle_id: str, set_id: str, *, publish: Publish = _noop) -> dict[str, Any]:
    row = get_set(circle_id, set_id)
    if row.status == "invalid":
        return {"ok": False, "denied": "an invalid proposal cannot go to a vote", "set_id": set_id}
    if row.status != "proposed":
        return {**vote_status(circle_id, set_id), "note": f"vote already {row.status}"}
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    rs = domain_set(row)
    now = VirtualClock(circle).now()
    with session_scope() as db:
        for m in charter.members:
            db.add(Vote(circle_id=circle_id, set_id=set_id, member=m.handle, kind="vote", choice="pending"))
        for h in rs.affected_members():
            db.add(Vote(circle_id=circle_id, set_id=set_id, member=h, kind="consent", choice="pending"))
        db.add(Vote(circle_id=circle_id, set_id=set_id, member=ORGANIZER, kind="approval", choice="pending"))
        live = db.get(RemedySetRow, set_id)
        if live is not None:
            live.status = "voting"
            live.vote_opened_at = now
            live.vote_closes_at = now + VOTE_WINDOW
            db.add(live)
    k, n = quorum_tuple(charter)
    affected = rs.affected_members()
    consent_note = f"; consent needed from {', '.join(affected)}" if affected else ""
    await _toast(publish, "info", f"Vote open: {k}/{n} yes votes needed{consent_note}")
    await publish({"type": "board"})
    return vote_status(circle_id, set_id)


def _choices(set_id: str, kind: str) -> dict[str, str]:
    return {v.member or "": v.choice or "pending" for v in _vote_rows(set_id, kind)}


def vote_status(circle_id: str, set_id: str) -> dict[str, Any]:
    row = get_set(circle_id, set_id)
    charter = state.load_charter(circle_id)
    rs = domain_set(row)
    k, n = quorum_tuple(charter)
    votes = _choices(set_id, "vote")
    yes = sum(1 for c in votes.values() if c == "yes")
    no = sum(1 for c in votes.values() if c == "no")
    consents = _choices(set_id, "consent")
    needed = rs.affected_members()
    given = [h for h in needed if consents.get(h) == "yes"]
    refused = [h for h in needed if consents.get(h) == "no"]
    approval = _choices(set_id, "approval").get(ORGANIZER, "pending")
    passed = yes >= k
    rejected = no > n - k or bool(refused)
    blockers: list[str] = []
    if row.status in ("proposed", "invalid"):
        blockers.append("the vote is not open")
    if not passed:
        blockers.append(f"{yes}/{k} yes votes")
    missing = [h for h in needed if h not in given]
    if missing:
        blockers.append(f"consent missing from {', '.join(missing)}")
    if approval != "yes":
        blockers.append("organizer approval missing")
    return {
        "ok": True,
        "set_id": set_id,
        "status": row.status,
        "votes": {
            "yes": yes,
            "no": no,
            "needed": k,
            "total": n,
            "passed": passed,
            "rejected": rejected,
            "closes_at": iso(row.vote_closes_at),
            "voters": [{"member": h, "choice": c} for h, c in votes.items()],
        },
        "consents": {"needed": needed, "given": given, "refused": refused},
        "approval": {
            "by": ORGANIZER,
            "status": "approved"
            if approval == "yes"
            else ("requested" if row.approval_requested_at else "pending"),
            "at": iso(row.approved_at),
        },
        "ready": not blockers and row.status in ("voting", "approved"),
        "blockers": blockers,
    }


def _set_choice(circle_id: str, set_id: str, kind: str, member: str, choice: str, source: str) -> None:
    with session_scope() as db:
        vote = db.exec(
            select(Vote).where(Vote.set_id == set_id, Vote.kind == kind, Vote.member == member)
        ).first()
        if vote is None:
            raise RecoveryError(f"{member} has no {kind} to give on {set_id}")
        vote.choice = choice
        vote.source = source
        vote.ts = utcnow()
        db.add(vote)


def _vote_open_or_raise(circle_id: str, set_id: str) -> RemedySetRow:
    row = get_set(circle_id, set_id)
    if row.status != "voting":
        raise RecoveryError(f"remedy set {set_id} is {row.status}, not open for voting")
    return row


async def cast_vote(
    circle_id: str, set_id: str, member: str, choice: str, *, source: str = "member", publish: Publish = _noop
) -> dict[str, Any]:
    if choice not in ("yes", "no"):
        raise ValueError("choice must be yes or no")
    row = _vote_open_or_raise(circle_id, set_id)
    circle = state.load_circle(circle_id)
    if row.vote_closes_at is not None and VirtualClock(circle).now() > as_utc(row.vote_closes_at):
        raise RecoveryError(f"the vote on {set_id} closed at {iso(row.vote_closes_at)}")
    _set_choice(circle_id, set_id, "vote", member, choice, source)
    status = vote_status(circle_id, set_id)
    await publish({"type": "board"})
    return status


async def record_consent(
    circle_id: str,
    set_id: str,
    member: str,
    choice: str = "yes",
    *,
    source: str = "member",
    publish: Publish = _noop,
) -> dict[str, Any]:
    if choice not in ("yes", "no"):
        raise ValueError("choice must be yes or no")
    _vote_open_or_raise(circle_id, set_id)
    _set_choice(circle_id, set_id, "consent", member, choice, source)
    await _toast(
        publish,
        "info" if choice == "yes" else "warn",
        f"{_name(circle_id, member)} {'consents' if choice == 'yes' else 'refuses'}",
    )
    await publish({"type": "board"})
    return vote_status(circle_id, set_id)


async def request_approval(circle_id: str, set_id: str, *, publish: Publish = _noop) -> dict[str, Any]:
    row = get_set(circle_id, set_id)
    if row.status not in ("voting", "approved"):
        return {
            "ok": False,
            "denied": f"remedy set {set_id} is {row.status}; open the vote first",
            "set_id": set_id,
        }
    with session_scope() as db:
        live = db.get(RemedySetRow, set_id)
        if live is not None and live.approval_requested_at is None:
            live.approval_requested_at = utcnow()
            db.add(live)
    await _toast(publish, "info", "Priya: one tap to approve the remedy once the vote passes")
    await publish({"type": "board"})
    return vote_status(circle_id, set_id)


async def approve(
    circle_id: str, set_id: str, *, by: str = ORGANIZER, execute: bool = True, publish: Publish = _noop
) -> dict[str, Any]:
    """The organizer's one tap. Refused until quorum and every affected member's consent are in.

    Holds the circle's recovery lock through execution, so two taps (two tabs) cannot run one set twice."""
    async with _circle_lock(circle_id):
        return await _approve_locked(circle_id, set_id, by=by, execute=execute, publish=publish)


async def _approve_locked(
    circle_id: str, set_id: str, *, by: str, execute: bool, publish: Publish
) -> dict[str, Any]:
    row = get_set(circle_id, set_id)
    if row.status == "executed" or row.status == "repaid":
        return {"ok": True, "already": True, **vote_status(circle_id, set_id)}
    if row.status != "voting" and row.status != "approved":
        return {
            "ok": False,
            "denied": f"remedy set {set_id} is {row.status}; nothing to approve",
            "set_id": set_id,
        }
    status = vote_status(circle_id, set_id)
    pre = [b for b in status["blockers"] if b != "organizer approval missing"]
    if pre:
        return {
            "ok": False,
            "denied": "approval needs " + "; ".join(pre),
            "set_id": set_id,
            **_strip_ok(status),
        }
    _set_choice(circle_id, set_id, "approval", ORGANIZER, "yes", "organizer")
    with session_scope() as db:
        live = db.get(RemedySetRow, set_id)
        if live is not None:
            live.status = "approved"
            live.approved_at = utcnow()
            live.approved_by = by
            db.add(live)
    await _toast(publish, "success", "Priya approved the remedy (one tap)")
    if not execute:
        await publish({"type": "board"})
        return vote_status(circle_id, set_id)
    return await _execute_locked(circle_id, set_id, publish=publish, pay_after=True)


def _strip_ok(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k != "ok"}


# --- execution ----------------------------------------------------------------------------------


async def execute_remedies(
    circle_id: str, set_id: str, *, publish: Publish = _noop, pay_after: bool = True
) -> dict[str, Any]:
    """Run an approved set. Refused without quorum, consents and the organizer's approval.

    Serialised per circle (``_circle_lock``) and claimed with a compare-and-set (approved -> executing), so a
    set executes at most once even when two requests race."""
    async with _circle_lock(circle_id):
        return await _execute_locked(circle_id, set_id, publish=publish, pay_after=pay_after)


async def _claim_for_execution(set_id: str) -> bool:
    """approved -> executing in one UPDATE; False when another run already claimed (or finished) the set."""
    from sqlalchemy import update

    with session_scope() as db:
        res = db.connection().execute(
            update(RemedySetRow)
            .where(RemedySetRow.id == set_id, RemedySetRow.status == "approved")  # type: ignore[arg-type]
            .values(status="executing")
        )
        return bool(res.rowcount)


async def _execute_locked(
    circle_id: str, set_id: str, *, publish: Publish, pay_after: bool
) -> dict[str, Any]:
    row = get_set(circle_id, set_id)
    if row.status in ("executed", "repaid", "executing"):
        return {"ok": True, "already": True, "set_id": set_id, "status": row.status}
    status = vote_status(circle_id, set_id)
    if row.status != "approved" or not status["ready"]:
        blockers = status["blockers"] or [f"remedy set is {row.status}"]
        return {"ok": False, "denied": "execution needs " + "; ".join(blockers), "set_id": set_id}

    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    rs = domain_set(row)
    consents = set(status["consents"]["given"])
    done = {r.remedy_id for r in _remedy_rows(set_id) if r.status in ("executed", "scheduled")}
    if not done:  # a partly applied set (reschedule_turns ran first) is re-checked step by step by the policy
        cs = recovery_state(circle_id, rs.round_n)
        problems = rd.validate_remedy_set(charter, cs, rs, consents=consents)
        if problems:
            return {"ok": False, "denied": "; ".join(problems), "set_id": set_id, "problems": problems}
    if not await _claim_for_execution(set_id):
        current = get_set(circle_id, set_id)
        return {"ok": True, "already": True, "set_id": set_id, "status": current.status}

    try:
        return await _run_remedies(circle_id, set_id, circle, charter, rs, consents, done, publish, pay_after)
    finally:
        with session_scope() as db:  # never leave a set stuck in "executing" (an exception mid-run)
            live = db.get(RemedySetRow, set_id)
            if live is not None and live.status == "executing":
                live.status = "failed"
                db.add(live)


async def _run_remedies(
    circle_id: str,
    set_id: str,
    circle: Circle,
    charter: Any,
    rs: rd.RemedySet,
    consents: set[str],
    done: set[str],
    publish: Publish,
    pay_after: bool,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    failed_money = False
    for remedy in sorted(rs.remedies, key=lambda r: EXEC_ORDER.get(r.type, 9)):
        if remedy.id in done:
            results.append(
                {"remedy_id": remedy.id, "ok": True, "type": remedy.type, "status": "already executed"}
            )
            continue
        cs = recovery_state(circle_id, rs.round_n)
        try:
            if remedy.type == "buffer_advance":
                res = await _apply_advance(circle_id, charter, cs, remedy, publish)
            elif remedy.type == "turn_swap":
                res = await _apply_swap(circle_id, circle, charter, cs, remedy, consents, set_id, publish)
            elif remedy.type == "repay_by_invoice":
                res = await _apply_invoice(circle_id, circle, charter, remedy, publish)
            else:
                _mark(remedy.id, "scheduled", f"Retry scheduled {remedy.params.due_date}")
                res = {
                    "ok": True,
                    "type": remedy.type,
                    "status": "scheduled",
                    "date": str(remedy.params.due_date),
                }
        except PolicyDenied as e:
            res = {"ok": False, "type": remedy.type, "denied": e.reason}
            _mark(remedy.id, "failed", f"Denied: {e.reason}"[:200])
        results.append({"remedy_id": remedy.id, **res})
        if not res.get("ok") and remedy.type in ("buffer_advance", "turn_swap"):
            failed_money = True
            await _toast(
                publish,
                "error",
                f"{lb.remedy_label(remedy.type).capitalize()} stopped: {res.get('denied') or res.get('error')}",
            )
            break

    final = "failed" if failed_money else "executed"
    with session_scope() as db:
        live = db.get(RemedySetRow, set_id)
        if live is not None:
            live.status = final
            live.executed_at = utcnow()
            live.result_json = json.dumps(results, default=str)[:20000]
            db.add(live)
    payout: dict[str, Any] | None = None
    if not failed_money:
        await _trace(
            publish,
            f"Remedy set {set_id} executed: "
            + ", ".join(f"{r.get('type')}={'ok' if r.get('ok') else 'failed'}" for r in results),
        )
        if pay_after:
            payout = await _maybe_pay(circle_id, rs.round_n, publish)
    await publish({"type": "board"})
    return {"ok": not failed_money, "set_id": set_id, "status": final, "results": results, "payout": payout}


async def reschedule_from_set(circle_id: str, set_id: str, *, publish: Publish = _noop) -> dict[str, Any]:
    """Apply only the approved set's turn swap (the reschedule_turns tool); execute_remedies skips it later."""
    row = get_set(circle_id, set_id)
    rs = domain_set(row)
    swaps = rs.of_type("turn_swap")
    if not swaps:
        return {"ok": False, "denied": f"remedy set {set_id} has no turn swap", "set_id": set_id}
    rows = {r.remedy_id: r for r in _remedy_rows(set_id)}
    swap = swaps[0]
    if rows.get(swap.id) is not None and rows[swap.id].status == "executed":
        return {
            "ok": True,
            "already": True,
            "set_id": set_id,
            "rotation": json.loads(_rotation_json(circle_id)),
        }
    status = vote_status(circle_id, set_id)
    if row.status != "approved" or not status["ready"]:
        blockers = status["blockers"] or [f"remedy set is {row.status}"]
        return {"ok": False, "denied": "rescheduling needs " + "; ".join(blockers), "set_id": set_id}
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    consents = set(status["consents"]["given"])
    cs = recovery_state(circle_id, rs.round_n)
    try:
        res = await _apply_swap(circle_id, circle, charter, cs, swap, consents, set_id, publish)
    except PolicyDenied as e:
        return {"ok": False, "denied": e.reason, "set_id": set_id}
    await publish({"type": "board"})
    return {"set_id": set_id, **res}


def _rotation_json(circle_id: str) -> str:
    circle = state.load_circle(circle_id)
    return circle.rotation_json or json.dumps(list(state.charter_of(circle).rotation))


def _mark(remedy_id: str, status: str, label: str | None = None, **fields: Any) -> dict[str, Any]:
    with session_scope() as db:
        row = db.exec(select(Remedy).where(Remedy.remedy_id == remedy_id)).first()
        if row is not None:
            row.status = status
            if label is not None:
                row.label = label
            if status in ("executed", "scheduled"):
                row.executed_at = utcnow()
            for k, v in fields.items():
                setattr(row, k, v)
            db.add(row)
    return {"ok": status != "failed", "status": status}


async def _apply_advance(
    circle_id: str, charter: Any, cs: Any, remedy: rd.Remedy, publish: Publish
) -> dict[str, Any]:
    amount = money(remedy.params.amount)
    PolicyEngine(charter, cs).authorize("buffer_advance", member=remedy.member, amount=amount)
    now = utcnow()
    with session_scope() as db:
        db.add(
            LedgerRow(
                circle_id=circle_id,
                kind="buffer_advance",
                amount=str(amount),
                round_n=remedy.round_n,
                member_handle=remedy.member,
                ref=f"remedy:{remedy.id}",
                ts=now,
            )
        )
        contrib = db.exec(
            select(Contribution).where(
                Contribution.circle_id == circle_id,
                Contribution.round_n == remedy.round_n,
                Contribution.member_handle == remedy.member,
            )
        ).first()
        if contrib is None:
            contrib = Contribution(circle_id=circle_id, round_n=remedy.round_n, member_handle=remedy.member)
        contrib.status = "COVERED"
        contrib.label = COVERED_LABEL
        contrib.updated_at = now
        db.add(contrib)
        db.flush()
        chip = _chip(contrib)
    _mark(remedy.id, "executed", f"Buffer advanced {amount}")
    state.bump_counter(circle_id, "shortfalls_covered")
    _settle_round_if_complete(circle_id, remedy.round_n)
    await publish(chip)
    await _toast(
        publish,
        "success",
        f"Buffer covered {_name(circle_id, remedy.member)}'s round {remedy.round_n} share ({lb.fmt_money(amount)})",
    )
    return {"ok": True, "type": "buffer_advance", "amount": str(amount), "member": remedy.member}


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


def _settle_round_if_complete(circle_id: str, round_n: int) -> None:
    charter = state.load_charter(circle_id)
    by_member = {c.member_handle: c.status for c in state.round_contributions(circle_id, round_n)}
    if all(by_member.get(m.handle) in state.SETTLED_CONTRIBUTION for m in charter.members):
        with session_scope() as db:
            rnd = db.exec(select(Round).where(Round.circle_id == circle_id, Round.n == round_n)).first()
            if rnd is not None and rnd.status in ("PENDING", "COLLECTING"):
                rnd.status = "COLLECTED"
                db.add(rnd)


async def _apply_swap(
    circle_id: str,
    circle: Circle,
    charter: Any,
    cs: Any,
    remedy: rd.Remedy,
    consents: set[str],
    set_id: str,
    publish: Publish,
) -> dict[str, Any]:
    partner = remedy.params.swap_with or ""
    PolicyEngine(charter, cs).authorize(
        "turn_swap", member=remedy.member, swap_with=partner, consents=consents
    )
    rotation = rd.swapped(rd.effective_rotation(charter, cs), remedy.member, partner)
    reschedule(circle_id, rotation, set_id=set_id)
    new_pos = {h: i + 1 for i, h in enumerate(rotation)}
    label = f"Turn moved to round {new_pos[remedy.member]} (swap with {partner})"
    _mark(remedy.id, "executed", label)
    await publish({"type": "schedule_changed", "rotation": rotation})
    await _toast(
        publish,
        "success",
        f"Rotation updated: {_name(circle_id, remedy.member)} -> round {new_pos[remedy.member]}, "
        f"{_name(circle_id, partner)} -> round {new_pos[partner]}",
    )
    return {
        "ok": True,
        "type": "turn_swap",
        "rotation": rotation,
        "member": remedy.member,
        "swap_with": partner,
    }


def reschedule(circle_id: str, rotation: list[str], *, set_id: str) -> None:
    """Persist a new payout order: Circle.rotation_json, unpaid Round recipients, member positions, and a
    re-signed mandate naming the new recipients (the organizer approved the swap with the remedy set)."""
    now = utcnow()
    with session_scope() as db:
        circle = db.get(Circle, circle_id)
        if circle is None:
            raise LookupError(f"circle {circle_id} not found")
        circle.rotation_json = json.dumps(rotation)
        db.add(circle)
        for rnd in db.exec(select(Round).where(Round.circle_id == circle_id)).all():
            if rnd.status != "PAID" and 1 <= rnd.n <= len(rotation):
                rnd.recipient_handle = rotation[rnd.n - 1]
                db.add(rnd)
        for m in db.exec(select(MemberRow).where(MemberRow.circle_id == circle_id)).all():
            if m.handle in rotation:
                m.rotation_pos = rotation.index(m.handle) + 1
                db.add(m)
        latest = db.exec(
            select(MandateRow).where(MandateRow.circle_id == circle_id).order_by(MandateRow.id.desc())  # type: ignore[attr-defined]
        ).first()
        if latest is not None:
            try:
                mm = importlib.import_module("app.domain.mandate")
                old = mm.Mandate.model_validate_json(latest.json)
                fresh = old.model_copy(
                    update={
                        "recipients": list(rotation),
                        "approved_at": now,
                        "approved_by": f"organizer one-tap (remedy {set_id})",
                        "signature": "",
                    }
                )
                signed = mm.sign_mandate(fresh, get_settings().mandate_hmac_secret)
                db.add(MandateRow(circle_id=circle_id, json=signed.model_dump_json(), approved_at=now))
            except Exception as e:  # noqa: BLE001 - payouts then need an explicit organizer approval
                log.warning("mandate re-sign after swap failed: %s", type(e).__name__)


def _invoice_id(created: dict[str, Any]) -> str | None:
    if created.get("id"):
        return str(created["id"])
    href = created.get("href")
    if not href:
        for link in created.get("links") or []:
            if isinstance(link, dict) and link.get("rel") in ("self", None):
                href = link.get("href")
                break
    if href:
        return str(href).rstrip("/").rsplit("/", 1)[-1] or None
    return None


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# PayPal's generate-qr-code answer is multipart/form-data: the PNG is base64 text in the part named "image",
# after that part's headers ("Content-Disposition: ...", "Content-Type: text/plain") and a blank line.
_MULTIPART_IMAGE_RX = re.compile(
    r'content-disposition:[^\r\n]*name="?image"?[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n(.*?)(?:\r?\n--|\Z)',
    re.IGNORECASE | re.DOTALL,
)


def _decodes(b64: str) -> bytes | None:
    try:
        return base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None


def _qr_data_url(result: Any) -> str | None:
    """The toolkit's generate_invoice_qr_code answer as a data: URL (PNG, base64), when it holds one.

    Accepts a data: URL, bare base64, or PayPal's raw multipart/form-data body (the adapter passes non-JSON
    answers through as text under "result")."""
    raw: Any = None
    if isinstance(result, dict):
        raw = result.get("result") or result.get("qr_code") or result.get("image")
    elif isinstance(result, str):
        raw = result
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("data:image"):
        return text
    candidates = [m.group(1) for m in _MULTIPART_IMAGE_RX.finditer(text)]
    # Otherwise: base64 runs, split at blank lines so a MIME header ("text/plain") never joins the payload.
    for chunk in re.split(r"\r?\n\s*\r?\n", text):
        candidates += re.findall(r"[A-Za-z0-9+/=\r\n]{100,}", chunk)
    candidates.append(text)
    fallback: str | None = None
    for cand in candidates:
        b64 = re.sub(r"\s+", "", cand)
        data = _decodes(b64) if b64 else None
        if data is None:
            continue
        if data.startswith(_PNG_MAGIC):
            return "data:image/png;base64," + b64
        fallback = fallback or b64
    return "data:image/png;base64," + fallback if fallback else None


def _payer_email(m: MemberRow | None) -> str | None:
    if m is None:
        return None
    for value in (m.email, m.sandbox_account, m.payout_receiver):
        if value and "@" in value:
            return value
    return None


def _error_label(e: BaseException) -> str:
    return str(getattr(e, "name", None) or type(e).__name__)


async def _apply_invoice(
    circle_id: str, circle: Circle, charter: Any, remedy: rd.Remedy, publish: Publish
) -> dict[str, Any]:
    member = remedy.member
    amount = money(remedy.params.amount or charter.amount)
    due = remedy.params.due_date or rd.next_weekday(_today(circle))
    due_label = f"Invoice due {due.strftime('%a')} {due.isoformat()}"
    mrow = _member_rows(circle_id).get(member)
    email = _payer_email(mrow)
    settings = get_settings()
    # A simulated round (Judge Mode without seeded vault ids) never triggers a live toolkit invoice either: the
    # decline it repays never happened on PayPal.
    simulated_round = settings.paypal_configured and _round_is_simulated(circle_id, remedy.round_n)
    simulated = not settings.paypal_configured or simulated_round
    invoice_id: str | None = None
    invoice_status = "SENT"
    qr: str | None = None
    if simulated:
        invoice_id = f"{SIMULATED_INVOICE_PREFIX}{circle.run_id}-{remedy.round_n}-{member}"
        why = (
            f"Round {remedy.round_n}'s charges were simulated"
            if simulated_round
            else "No PayPal sandbox credentials"
        )
        await _trace(publish, f"{why}: invoice simulated (labelled), no PayPal call made")
    else:
        keys = _keys()
        number = keys.invoice_number(circle.run_id, circle_id, remedy.round_n, member)
        base_rid = f"tk:{circle.run_id}:{circle_id}:{remedy.round_n}:{member}"
        name = (mrow.name if mrow else member).split()
        recipient: dict[str, Any] = {"name": {"given_name": name[0], "surname": " ".join(name[1:]) or None}}
        if email:
            recipient["email_address"] = email
        params = {
            "currency_code": charter.currency,
            "invoice_number": number,
            "invoice_date": _today(circle).isoformat(),
            "reference": f"{circle_id}:{remedy.round_n}:{member}",
            "note": f"{charter.name}: repays the buffer advance for round {remedy.round_n}. Due {due.isoformat()}.",
            "invoicer_business_name": f"Turnkeeper · {charter.name}"[:300],
            "primary_recipients": [{"billing_info": recipient}],
            "items": [
                {
                    "name": f"Round {remedy.round_n} contribution",
                    "quantity": "1",
                    "unit_amount": {"currency_code": charter.currency, "value": str(amount)},
                }
            ],
        }
        try:
            tk = _toolkit()
            await tk.prepare()
            created = await tk.run("create_invoice", params, request_id=f"{base_rid}:inv")
            invoice_id = _invoice_id(created)
            if not invoice_id:
                raise ValueError("create_invoice returned no invoice id")
            await _trace(
                publish, f"toolkit.create_invoice -> {invoice_id}", kind="tool_result", tool="create_invoice"
            )
            await tk.run(
                "send_invoice",
                {"invoice_id": invoice_id, "send_to_recipient": bool(email), "note": due_label},
                request_id=f"{base_rid}:invsend",
            )
            await _trace(
                publish, f"toolkit.send_invoice -> {invoice_id} SENT", kind="tool_result", tool="send_invoice"
            )
            try:
                qr_raw = await tk.run(
                    "generate_invoice_qr_code",
                    {"invoice_id": invoice_id, "width": 300, "height": 300},
                    request_id=f"{base_rid}:invqr",
                )
                qr = _qr_data_url(qr_raw)
                await _trace(
                    publish,
                    f"toolkit.generate_invoice_qr_code -> {'QR ready' if qr else 'no image in response'}",
                    kind="tool_result",
                    tool="generate_invoice_qr_code",
                )
            except Exception as e:  # noqa: BLE001 - the invoice stands without its QR
                log.warning("invoice QR failed for %s: %s", invoice_id, _error_label(e))
        except Exception as e:  # noqa: BLE001 - reported on the remedy; the advance already covered the round
            label = f"Invoice failed: {_error_label(e)}"
            log.warning("invoice for %s round %s failed: %s", member, remedy.round_n, _error_label(e))
            _mark(
                remedy.id,
                "failed",
                label,
                invoice_id=invoice_id,
                invoice_status="DRAFT" if invoice_id else "FAILED",
            )
            await _toast(publish, "error", label)
            return {
                "ok": False,
                "type": "repay_by_invoice",
                "error": _error_label(e),
                "invoice_id": invoice_id,
            }
    _mark(
        remedy.id,
        "executed",
        due_label + (" (simulated)" if simulated else ""),
        invoice_id=invoice_id,
        invoice_status=invoice_status,
        invoice_qr=qr,
        invoice_simulated=simulated,
    )
    lang = mrow.lang if mrow else "en"
    try:
        inbox.add_message(
            circle_id,
            member,
            inbox.outbound_text(
                "invoice",
                lang,
                name=(mrow.name.split()[0] if mrow else member),
                amount=f"${amount}",
                due=due.isoformat(),
            ),
            lang=lang,
            direction="out",
            source="rules",
            round_n=remedy.round_n,
        )
    except Exception as e:  # noqa: BLE001 - the inbox line is cosmetic
        log.warning("invoice inbox message failed: %s", e)
    await _toast(
        publish,
        "success",
        f"PayPal invoice sent to {_name(circle_id, member)}: {lb.fmt_money(amount)} {charter.currency}, "
        f"due {due.strftime('%a')}" + (" (simulated)" if simulated else " (Agent Toolkit)"),
    )
    await publish({"type": "board"})
    return {
        "ok": True,
        "type": "repay_by_invoice",
        "invoice_id": invoice_id,
        "status": invoice_status,
        "qr": bool(qr),
        "simulated": simulated,
        "due_date": due.isoformat(),
        "amount": str(amount),
    }


async def _maybe_pay(circle_id: str, round_n: int, publish: Publish) -> dict[str, Any] | None:
    """Pay the round once the remedy settled it (same gates as every payout: mandate, ledger, balances, policy)."""
    contribs = state.round_contributions(circle_id, round_n)
    charter = state.load_charter(circle_id)
    by_member = {c.member_handle: c.status for c in contribs}
    if not all(by_member.get(m.handle) in state.SETTLED_CONTRIBUTION for m in charter.members):
        return None
    rnd = state.get_round(circle_id, round_n)
    if rnd is None or rnd.status == "PAID" or state.live_payout_exists(rnd):
        return None
    if not get_settings().paypal_configured:
        await _trace(publish, "Round settled by the remedy; payout skipped (no PayPal sandbox credentials)")
        return {"ok": False, "skipped": "no PayPal sandbox credentials"}
    if _round_is_simulated(circle_id, round_n):
        # Never move real sandbox money on contributions that were only simulated (Judge Mode, no vault ids).
        await _trace(
            publish, "Round settled by the remedy; payout skipped (the round's charges were simulated)"
        )
        return {"ok": False, "skipped": "simulated round: no live charges to pay out"}
    await _trace(publish, f"Round {round_n} is settled by the remedy; paying the pot through the usual gates")
    try:
        return await _rounds().pay_round(circle_id, round_n, publish=publish)
    except Exception as e:  # noqa: BLE001 - the remedy stands; the payout can be retried
        log.warning("payout after remedy failed: %s", _error_label(e))
        return {"ok": False, "error": _error_label(e)}


# --- repayment ----------------------------------------------------------------------------------


def _find_invoice_remedy(invoice_id: str) -> Remedy | None:
    with session_scope() as db:
        return db.exec(select(Remedy).where(Remedy.invoice_id == invoice_id)).first()


def _invoice_amounts(resource: dict[str, Any]) -> tuple[str | None, Decimal | None, Decimal | None]:
    inv = resource.get("invoice") if isinstance(resource.get("invoice"), dict) else resource
    invoice_id = inv.get("id")
    payments = inv.get("payments") or {}
    paid = (payments.get("paid_amount") or {}).get("value")
    if paid is None:
        paid = ((inv.get("amount") or {}).get("value")) if isinstance(inv.get("amount"), dict) else None
    fee = None
    for tx in payments.get("transactions") or []:
        fee_value = ((tx.get("payment_fee") or {}) if isinstance(tx, dict) else {}).get("value")
        if fee_value is not None:
            fee = money(fee_value) + (fee or Decimal("0.00"))
    return (str(invoice_id) if invoice_id else None, money(paid) if paid is not None else None, fee)


async def on_invoice_paid(
    invoice_id: str,
    *,
    paid_amount: Decimal | None = None,
    fee: Decimal | None = None,
    simulated: bool = False,
    publish_for: Any | None = None,
) -> dict[str, Any]:
    """Record the repayment: ``advance_repaid`` (net) restores the buffer; the round reads "covered, not missed"."""
    row = _find_invoice_remedy(invoice_id)
    if row is None:
        return {"handled": False, "reason": "invoice not ours"}
    if publish_for is None:
        from app.engine.sessions import circle_publisher

        publish_for = circle_publisher
    publish = publish_for(row.circle_id)
    if row.invoice_status == "PAID":
        return {"handled": True, "duplicate": True, "circle_id": row.circle_id, "invoice_id": invoice_id}
    remedy = rd.Remedy.model_validate_json(row.json)
    charter = state.load_charter(row.circle_id)
    gross = money(paid_amount if paid_amount is not None else (remedy.params.amount or charter.amount))
    fee_amt = money(fee) if fee is not None else estimate_fee(gross)
    net = net_of(gross, fee_amt) if fee_amt < gross else Decimal("0.00")
    open_adv = state.open_advances(row.circle_id).get(remedy.member, Decimal("0.00"))
    repaid = money(min(net, open_adv)) if open_adv > 0 else Decimal("0.00")
    label = covered_not_missed(remedy.round_n)
    with session_scope() as db:
        if repaid > 0:
            db.add(
                LedgerRow(
                    circle_id=row.circle_id,
                    kind="advance_repaid",
                    amount=str(repaid),
                    round_n=remedy.round_n,
                    member_handle=remedy.member,
                    ref=("simulated:" if simulated else "") + invoice_id,
                    ts=utcnow(),
                )
            )
        live = db.get(Remedy, row.id)
        if live is not None:
            live.invoice_status = "PAID"
            live.label = "Invoice paid" + (" (simulated)" if simulated else "")
            if simulated:
                live.invoice_simulated = True
            db.add(live)
        contrib = db.exec(
            select(Contribution).where(
                Contribution.circle_id == row.circle_id,
                Contribution.round_n == remedy.round_n,
                Contribution.member_handle == remedy.member,
            )
        ).first()
        chip = None
        if contrib is not None and contrib.status == "COVERED":
            contrib.label = label
            contrib.updated_at = utcnow()
            db.add(contrib)
            chip = _chip(contrib)
        rs_row = db.get(RemedySetRow, row.set_id) if row.set_id else None
        if rs_row is not None and rs_row.status == "executed":
            rs_row.status = "repaid"
            db.add(rs_row)
    if chip:
        await publish(chip)
    how = "simulated in sandbox" if simulated else "webhook"
    await _toast(
        publish,
        "success",
        f"{_name(row.circle_id, remedy.member)} paid the invoice ({how}): buffer +{lb.fmt_money(repaid)}. {label}",
    )
    await publish({"type": "board"})
    return {
        "handled": True,
        "circle_id": row.circle_id,
        "member": remedy.member,
        "round_n": remedy.round_n,
        "invoice_id": invoice_id,
        "repaid": str(repaid),
        "simulated": simulated,
        "label": label,
    }


async def handle_invoice_paid_event(resource: dict[str, Any], publish_for: Any) -> dict[str, Any]:
    """Dispatch target for the INVOICING.INVOICE.PAID webhook (app.engine.webhooks)."""
    invoice_id, paid, fee = _invoice_amounts(resource or {})
    if not invoice_id:
        return {"handled": False, "reason": "no invoice id"}
    return await on_invoice_paid(invoice_id, paid_amount=paid, fee=fee, publish_for=publish_for)


def current_invoice(circle_id: str) -> Remedy | None:
    with session_scope() as db:
        return db.exec(
            select(Remedy)
            .where(Remedy.circle_id == circle_id, Remedy.invoice_id.is_not(None))  # type: ignore[union-attr]
            .order_by(Remedy.id.desc())  # type: ignore[union-attr]
        ).first()


def sent_invoice(circle_id: str) -> Remedy | None:
    """The latest invoice Ravi really received: an executed repay_by_invoice remedy whose invoice is SENT (or
    already PAID). A failed or draft invoice never counts."""
    with session_scope() as db:
        return db.exec(
            select(Remedy)
            .where(
                Remedy.circle_id == circle_id,
                Remedy.invoice_id.is_not(None),  # type: ignore[union-attr]
                Remedy.status == "executed",
                Remedy.invoice_status.in_(("SENT", "PAID")),  # type: ignore[union-attr]
            )
            .order_by(Remedy.id.desc())  # type: ignore[union-attr]
        ).first()


async def simulate_invoice_paid(circle_id: str, *, publish: Publish) -> dict[str, Any]:
    """Judge Mode: act as if INVOICING.INVOICE.PAID arrived (sandbox invoices are not paid by a real payer)."""
    row = sent_invoice(circle_id)
    if row is None or not row.invoice_id:
        latest = current_invoice(circle_id)
        if latest is not None:
            raise RecoveryError(
                f"invoice was not sent ({latest.invoice_status or latest.status}); nothing to mark paid"
            )
        raise RecoveryError("no invoice to mark paid; execute a repay_by_invoice remedy first")
    return await on_invoice_paid(row.invoice_id, simulated=True, publish_for=lambda _cid: publish)


# --- rules-mode sequence ------------------------------------------------------------------------


def send_decline_notice(circle_id: str, round_n: int, member: str) -> dict[str, Any] | None:
    """The member's "your contribution was declined, when can you pay?" message, once per round.

    Sent when the decline happens (``rounds.charge_member``), so the member's reply always follows it in the
    inbox; ``run_rules_recovery`` calls it again as a no-op safety net. Returns the new message or None.
    """
    for msg in inbox.list_messages(circle_id, member=member, direction="out", limit=50):
        if msg.get("round_n") == round_n:
            return None
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    mrow = _member_rows(circle_id).get(member)
    lang = mrow.lang if mrow else "en"
    return inbox.add_message(
        circle_id,
        member,
        inbox.outbound_text(
            "decline",
            lang,
            name=(mrow.name.split()[0] if mrow else member),
            round_n=round_n,
            amount=f"${charter.amount}",
        ),
        lang=lang,
        direction="out",
        source="rules",
        round_n=round_n,
    )


SIMULATED_DECLINE_LABEL = "API decline: simulated (no live sandbox for this circle)"
SIMULATED_CAPTURE_LABEL = "Captured (simulated, no PayPal call)"
SIMULATED_REQUEST_PREFIX = "REPLAY-SIM-"  # request ids of simulated rows (see _round_is_simulated)


def _round_is_simulated(circle_id: str, round_n: int) -> bool:
    return any(
        (c.request_id or "").startswith(SIMULATED_REQUEST_PREFIX)
        for c in state.round_contributions(circle_id, round_n)
    )


async def simulate_round_decline(
    circle_id: str, round_n: int, member: str, *, publish: Publish = _noop
) -> dict[str, Any]:
    """Judge Mode without a live sandbox (no credentials or no seeded vault ids): settle the round the way the
    fixture settles rounds 1-2 (labelled ``REPLAY-`` ids, no PayPal call) except ``member``, who is DECLINED
    with INSTRUMENT_DECLINED (decline_source "simulated"). Then the decline notice goes out, so steps 3-8 of the
    story (reply, recovery, consent, vote, approval, repayment) work. Idempotent for the same member."""
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    if member not in {m.handle for m in charter.members}:
        raise RecoveryError(f"unknown member {member!r}")
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        raise RecoveryError(f"round {round_n} not found")
    if rnd.status in ("PAID", "VOID"):
        raise RecoveryError(f"round {round_n} is {rnd.status}")
    contribs = {c.member_handle: c for c in state.round_contributions(circle_id, round_n)}
    target = contribs.get(member)
    if target is None:
        raise RecoveryError(f"{member} has no contribution in round {round_n}")
    if target.status == "DECLINED":
        return {"ok": True, "already": True, "round_n": round_n, "member": member, "status": "DECLINED"}
    if target.status != "PENDING":
        raise RecoveryError(f"{member} is already {target.status} in round {round_n}")

    amount = money(charter.amount)
    fee = estimate_fee(amount)
    net = net_of(amount, fee)
    now = utcnow()
    virtual_now = VirtualClock(circle).now()
    run_id = circle.run_id
    chips: list[dict[str, Any]] = []
    captured: list[str] = []
    with session_scope() as db:
        for m in charter.members:
            c = db.get(Contribution, contribs[m.handle].id) if m.handle in contribs else None
            if c is None:
                continue
            if m.handle == member:
                c.status = "DECLINED"
                c.attempt = max(1, c.attempt)
                c.decline_code = "INSTRUMENT_DECLINED"
                c.decline_source = "simulated"
                c.label = SIMULATED_DECLINE_LABEL
                c.order_id = None
                c.capture_id = None
                c.request_id = f"{SIMULATED_REQUEST_PREFIX}{run_id}-{round_n}-{m.handle}"
                c.last_attempt_at = virtual_now
            elif c.status == "PENDING":
                handle = m.handle.upper()
                c.status = "COMPLETED"
                c.attempt = max(1, c.attempt)
                c.order_id = f"REPLAY-ORD-{round_n}-{handle}"
                c.capture_id = f"REPLAY-CAP-{round_n}-{handle}"
                c.gross, c.fee, c.net = str(amount), str(fee), str(net)
                c.decline_code = None
                c.decline_source = None
                c.webhook_verified = False
                c.label = SIMULATED_CAPTURE_LABEL
                c.request_id = f"{SIMULATED_REQUEST_PREFIX}{run_id}-{round_n}-{m.handle}"
                c.last_attempt_at = virtual_now
                db.add(
                    LedgerRow(
                        circle_id=circle_id,
                        kind="contribution_net",
                        amount=str(net),
                        round_n=round_n,
                        member_handle=m.handle,
                        ref=c.capture_id,
                        ts=now,
                    )
                )
                captured.append(m.handle)
            else:
                continue
            c.updated_at = now
            db.add(c)
            chips.append(_chip(c))
        live_round = db.get(Round, rnd.id)
        if live_round is not None:
            if live_round.status == "PENDING":
                live_round.status = "COLLECTING"
            if captured:
                live_round.last_capture_at = now
            db.add(live_round)
    for chip in chips:
        await publish(chip)
    send_decline_notice(circle_id, round_n, member)
    await _trace(
        publish,
        f"Judge: round {round_n} simulated without PayPal ({len(captured)} captures labelled REPLAY-, "
        f"{member} declined: INSTRUMENT_DECLINED, simulated). No sandbox call was made.",
    )
    await _toast(
        publish,
        "warn",
        f"Round {round_n} simulated: {_name(circle_id, member)}'s charge declined (no sandbox call)",
    )
    await publish({"type": "board"})
    return {
        "ok": True,
        "round_n": round_n,
        "member": member,
        "status": "DECLINED",
        "captured": captured,
        "simulated": True,
        "label": SIMULATED_DECLINE_LABEL,
    }


async def run_rules_recovery(
    circle_id: str, round_n: int | None = None, member: str | None = None, *, publish: Publish = _noop
) -> dict[str, Any]:
    """Deterministic recovery up to the human gates: notice -> commitment -> default proposal -> vote -> approval
    request. Consents, votes and Priya's tap come from people (or Judge Mode); execution follows the tap."""
    if round_n is None or member is None:
        found = find_shortfall(circle_id)
        if found is None:
            await _trace(publish, "Recovery: no declined contribution to remedy", kind="text")
            return {"ok": False, "error": "NO_SHORTFALL"}
        round_n = round_n or found[0]
        member = member or found[1]
    circle = state.load_circle(circle_id)
    mrow = _member_rows(circle_id).get(member)
    lang = mrow.lang if mrow else "en"
    charter = state.charter_of(circle)

    if send_decline_notice(circle_id, round_n, member) is not None:
        await _trace(publish, f"Asked {member} (in {lang}) when they can pay", kind="text")

    reply = inbox.latest_reply(circle_id, member)
    commitment = None
    if reply:
        payday = inbox.parse_payday(
            reply.get("text") or "", today=_today(circle), payday_rule=mrow.payday_rule if mrow else None
        )
        if payday is not None:
            rec = record_commitment(
                circle_id,
                member,
                charter.amount,
                payday,
                source="rules",
                message_id=reply.get("id"),
                round_n=round_n,
            )
            if rec.get("ok"):
                commitment = rec["commitment"]
                await _trace(
                    publish,
                    f"record_commitment: {member} pays {commitment['amount']} on {commitment['date']} (from the reply)",
                    kind="tool_result",
                    tool="record_commitment",
                )
    proposal = await propose(circle_id, round_n, member, None, source="rules", publish=publish)
    if not proposal.get("ok"):
        return {"ok": False, "proposal": proposal, "commitment": commitment}
    set_id = proposal["set_id"]
    vote = await open_vote(circle_id, set_id, publish=publish)
    await request_approval(circle_id, set_id, publish=publish)
    waiting = vote.get("blockers") or []
    await _trace(publish, f"Recovery {set_id} waiting on: {'; '.join(waiting)}", kind="text")
    return {"ok": True, "set_id": set_id, "proposal": proposal, "commitment": commitment, "vote": vote}


async def _finish_vote_opening(
    rec: Any, row: RemedySetRow, publish: Publish, *, result_mode: str | None
) -> None:
    """After an agent proposal: open the vote and request Priya's approval when the agent did not."""
    finished: list[str] = []
    if row.status == "proposed":
        await open_vote(rec.circle_id, row.id, publish=publish)
        finished.append("opened the vote")
    live = get_set(rec.circle_id, row.id)
    if live.status == "voting" and live.approval_requested_at is None:
        await request_approval(rec.circle_id, row.id, publish=publish)
        finished.append("requested the organizer's approval")
    if finished:
        await _trace(publish, f"Rules finished the vote opening: {' and '.join(finished)} for {row.id}")
    # The proposal itself came from the model: keep its mode (live or replay) even when a later turn fell back.
    if result_mode not in ("live", "replay"):
        result_mode = "live"
    rec.mode = result_mode
    rec.ok = True
    rec.summary = rec.summary or f"Proposed {row.id}; waiting on consents, votes and organizer approval"


async def recover_runner(
    rec: Any, run_id: str, publish: Publish, fallback_engine: bool, *, member: str | None = None
) -> None:
    """``app.engine.tasks.start_task`` runner for kind "recover": the agent first; when it leaves no proposal
    (the agent lacks the recovery tools, no model is configured, or it failed) the rules-mode sequence runs."""
    tasks = importlib.import_module("app.engine.tasks")
    before = latest_set(rec.circle_id, include_superseded=True)
    before_id = before.id if before else None
    agent_mode: str | None = None
    try:
        available, why = tasks.agent_available()
        if available:
            try:
                agent = importlib.import_module("app.agent.treasurer")
                task = agent.AgentTask(
                    kind="recover",
                    circle_id=rec.circle_id,
                    round_n=rec.round_n,
                    sid=rec.sid,
                    context={"member": member} if member else {},  # no run_id: keeps replays cross-session
                )
                result = await agent.run_task(
                    task, publish=publish, live_allowed=getattr(rec, "live_allowed", True)
                )
                rec.ok = bool(getattr(result, "ok", False))
                rec.mode = getattr(result, "mode", None)
                rec.summary = getattr(result, "summary", None)
                model_modes = [
                    e.get("mode") for e in (getattr(result, "trace", None) or []) if isinstance(e, dict)
                ]
                model_modes = [m for m in model_modes if m in ("live", "replay")]
                if model_modes:  # the turns that came from a model (before any rules fallback)
                    agent_mode = "replay" if all(m == "replay" for m in model_modes) else "live"
            except Exception as e:  # noqa: BLE001 - fall through to the rules sequence
                log.warning("recover agent run failed: %s", type(e).__name__, exc_info=True)
                await _trace(publish, f"agent recover run failed ({type(e).__name__}); using rules mode")
        after = latest_set(rec.circle_id, include_superseded=True)
        if after is not None and after.id != before_id and after.status in ("proposed", "voting"):
            # The agent proposed but stopped before the vote (rate limit, replay miss, tool cap): finish the
            # deterministic part so the judge's Consent/Vote buttons always have an open vote to act on.
            await _finish_vote_opening(rec, after, publish, result_mode=agent_mode or rec.mode)
        elif after is None or after.id == before_id or after.status == "invalid":
            if after is not None and after.id != before_id:
                note = "agent's proposal is not charter-legal"
            else:
                note = "agent left no remedy proposal" if available else why
            await _trace(publish, f"{note}; running the rules-mode recovery sequence")
            out = await run_rules_recovery(rec.circle_id, rec.round_n, member, publish=publish)
            rec.ok = bool(out.get("ok"))
            rec.mode = "rules"
            rec.summary = (
                f"Proposed {out.get('set_id')}; waiting on consents, votes and organizer approval"
                if out.get("ok")
                else f"Recovery stopped: {out.get('error') or (out.get('proposal') or {}).get('denied')}"
            )
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        log.exception("recover task %s failed", rec.task_id)
        rec.ok = False
        rec.error = f"{type(e).__name__}: {e}"
        await _toast(publish, "error", f"recovery failed: {type(e).__name__}")
    finally:
        rec.done = True
        rec.finished_at = utcnow()
        await publish({"type": "board"})


# --- read models --------------------------------------------------------------------------------


def _invoice_view(circle_id: str, row: Remedy | None, *, with_qr: bool) -> dict[str, Any] | None:
    if row is None or not row.invoice_id:
        return None
    remedy = rd.Remedy.model_validate_json(row.json)
    out: dict[str, Any] = {
        "id": row.invoice_id,
        "status": row.invoice_status,
        "member": row.member,
        "round_n": row.round_n,
        "amount": str(remedy.params.amount) if remedy.params.amount is not None else None,
        "due_date": remedy.params.due_date.isoformat() if remedy.params.due_date else None,
        "simulated": bool(row.invoice_simulated),
        "qr": f"/api/circles/{circle_id}/recovery/qr" if row.invoice_qr else None,
    }
    if with_qr:
        out["qr_data"] = row.invoice_qr
    return out


def recovery_view(circle_id: str, *, with_qr: bool = False) -> dict[str, Any]:
    """BoardModel ``recovery`` block (``with_qr`` adds the QR data URL for GET /recovery)."""
    row = latest_set(circle_id)
    if row is None:
        return {
            "status": "none",
            "set_id": None,
            "member": None,
            "round_n": None,
            "commitment": None,
            "remedies": [],
            "problems": [],
            "votes": None,
            "consents": None,
            "approval": None,
            "invoice": None,
        }
    rs = domain_set(row)
    rows = {r.remedy_id: r for r in _remedy_rows(row.id)}
    status = vote_status(circle_id, row.id)
    invoice_row = next((r for r in rows.values() if r.invoice_id), None)
    return {
        "status": row.status,
        "set_id": row.id,
        "member": row.member,
        "round_n": row.round_n,
        "source": row.source,
        "commitment": json.loads(row.commitment_json) if row.commitment_json else None,
        "remedies": [_remedy_view(r, rows.get(r.id)) for r in rs.remedies],
        "problems": json.loads(row.problems_json or "[]"),
        "votes": {k: status["votes"][k] for k in ("yes", "no", "needed", "total", "passed", "closes_at")},
        "consents": status["consents"],
        "approval": status["approval"],
        "blockers": status["blockers"],
        "invoice": _invoice_view(circle_id, invoice_row, with_qr=with_qr),
    }


def member_labels(circle_id: str) -> dict[str, str | None]:
    """members[].label: covered rounds, moved turns and open invoices, joined with " · "."""
    labels: dict[str, list[str]] = {}
    with session_scope() as db:
        covered = db.exec(
            select(Contribution)
            .where(Contribution.circle_id == circle_id, Contribution.status == "COVERED")
            .order_by(Contribution.round_n)
        ).all()
        executed = db.exec(
            select(Remedy)
            .where(Remedy.circle_id == circle_id, Remedy.status == "executed")
            .order_by(Remedy.id)
        ).all()
    for c in covered:
        text = (
            c.label
            if (c.label or "").startswith("Round ")
            else f"Round {c.round_n}: {(c.label or COVERED_LABEL).lower()}"
        )
        labels.setdefault(c.member_handle, []).append(text)
    rotation_pos = {m.handle: m.rotation_pos for m in state.members_of(circle_id)}
    for r in executed:
        remedy = rd.Remedy.model_validate_json(r.json)
        if r.type == "turn_swap" and remedy.params.swap_with:
            a, b = remedy.member, remedy.params.swap_with
            labels.setdefault(a, []).append(f"Turn moved to round {rotation_pos.get(a)} (swap with {b})")
            labels.setdefault(b, []).append(f"Turn moved to round {rotation_pos.get(b)} (swap with {a})")
        elif r.type == "repay_by_invoice" and r.invoice_status != "PAID" and remedy.params.due_date:
            labels.setdefault(remedy.member, []).append(
                f"Invoice due {remedy.params.due_date.strftime('%a')}"
            )
    return {h: " · ".join(v) for h, v in labels.items()}
