"""Member mandates in the database: seed, load, verify, view. Active only with the ``member_mandates`` flag.

Everything here is additive: one new table (``MemberMandateRow``), nothing in the existing tables changes, and
with the flag off nothing in this module runs (``state.circle_state`` leaves ``member_mandate_ok`` at None).

Labels (docs/CONTRACTS.md "Member mandates"): ``MANDATE_LABEL`` and ``SIGNATURE_LABEL`` travel with every view.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlmodel import Session as DbSession
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.domain import charter as cm
from app.domain import member_mandate as mm
from app.engine import state
from app.engine.clock import VirtualClock
from app.engine.common import iso, utcnow
from app.models import Circle, MemberMandateRow

log = logging.getLogger("turnkeeper.engine.member_mandates")

GRACE_DAYS = 30  # like the organizer mandate: valid until 30 days after the last round is due


def enabled(circle_id: str) -> bool:
    """The feature is on for this circle (flag or env), and not killed by TK_MEMBER_AGENTS=0."""
    try:
        return state.member_mandates_enabled(state.load_circle(circle_id))
    except LookupError:
        return False


def killed() -> bool:
    return get_settings().member_agents_killed


def _secret() -> str:
    return get_settings().mandate_hmac_secret


def default_expiry(charter: Any) -> date:
    return cm.due_date(charter, cm.rounds_total(charter)) + timedelta(days=GRACE_DAYS)


# --- seeding --------------------------------------------------------------------------------------------


def seed_in(db: DbSession, circle: Circle, charter: Any, now: datetime, *, only_missing: bool = False) -> int:
    """Insert signed default mandates for the circle's members (inside the caller's transaction)."""
    have: set[str] = set()
    if only_missing:
        have = {r.member for r in db.exec(select(MemberMandateRow).where(MemberMandateRow.circle_id == circle.id))}
    mandates = mm.default_member_mandates(
        charter,
        circle_id=circle.id,
        charter_sha256=circle.charter_sha256,
        approved_at=now,
        expires=default_expiry(charter),
        secret=_secret(),
        members=[m.handle for m in charter.members if m.handle not in have],
    )
    for m in mandates:
        db.add(MemberMandateRow(circle_id=circle.id, member=m.member, json=m.model_dump_json(), approved_at=now))
    return len(mandates)


def wanted_at_clone() -> bool:
    """Seed mandates when a fixture circle is cloned: the global flag is on and the kill switch is not."""
    s = get_settings()
    return bool(s.member_mandates and not s.member_agents_killed)


def ensure_seeded(circle_id: str) -> int:
    """Idempotent: add default mandates for members that have none (e.g. after the circle flag was switched on)."""
    with session_scope() as db:
        circle = db.get(Circle, circle_id)
        if circle is None:
            raise LookupError(f"circle {circle_id} not found")
        return seed_in(db, circle, state.charter_of(circle), utcnow(), only_missing=True)


def enable(circle_id: str) -> dict[str, Any]:
    """Switch the circle flag on and seed its default mandates (Story B and Advanced Judge)."""
    if killed():
        raise PermissionError("member mandates are switched off (TK_MEMBER_AGENTS=0)")
    state.set_flag(circle_id, state.MEMBER_MANDATES_FLAG, True)
    return {"seeded": ensure_seeded(circle_id)}


# --- reading --------------------------------------------------------------------------------------------


def _latest_rows(circle_id: str) -> dict[str, MemberMandateRow]:
    with session_scope() as db:
        rows = db.exec(
            select(MemberMandateRow).where(MemberMandateRow.circle_id == circle_id).order_by(MemberMandateRow.id)
        ).all()
    return {r.member: r for r in rows}  # newest row per member wins


def parse_row(row: MemberMandateRow | None) -> mm.MemberMandate | None:
    if row is None:
        return None
    try:
        return mm.MemberMandate.model_validate_json(row.json)
    except Exception:  # noqa: BLE001 - an unreadable row is "no valid mandate", never a crash
        return None


def load_mandate(circle_id: str, member: str) -> mm.MemberMandate | None:
    return parse_row(_latest_rows(circle_id).get(member))


def check(circle: Circle, charter: Any, member: str, m: mm.MemberMandate | None) -> dict[str, Any]:
    return mm.policy_entry(
        m,
        _secret(),
        circle_id=circle.id,
        member=member,
        charter_sha256=cm.charter_sha256(charter),
        today=VirtualClock(circle).today(),
    )


def policy_map(circle: Circle, charter: Any) -> dict[str, dict[str, Any]]:
    """``CircleState.member_mandate_ok`` for every charter member (verified against the CURRENT charter hash)."""
    rows = _latest_rows(circle.id)
    return {
        m.handle: check(circle, charter, m.handle, parse_row(rows.get(m.handle))) for m in charter.members
    }


def mandates_view(circle_id: str) -> dict[str, Any]:
    """GET /api/circles/{id}/mandates: each member's mandate, the signature check and the honest labels."""
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    rows = _latest_rows(circle_id)
    members = []
    for m in charter.members:
        mandate = parse_row(rows.get(m.handle))
        entry = check(circle, charter, m.handle, mandate)
        members.append(
            {
                "member": m.handle,
                "name": m.name,
                "present": mandate is not None,
                "signature_verified": bool(entry["ok"]),
                "status": entry["reason"],
                "max_charge_per_round": str(mandate.max_charge_per_round) if mandate else None,
                "max_advance": str(mandate.max_advance) if mandate else None,
                "expires": mandate.expires.isoformat() if mandate else None,
                "approved_at": iso(mandate.approved_at) if mandate else None,
                "charter_sha256": mandate.charter_sha256 if mandate else None,
                "rules": (
                    {
                        "accept_earlier_swap": mandate.accept_earlier_swap,
                        "max_advance": str(mandate.max_advance),
                        "require_ask_for_later_swap": mandate.require_ask_for_later_swap,
                        "summary": mandate.rule_summary,
                    }
                    if mandate
                    else None
                ),
                "signature": (mandate.signature[:16] + "…") if mandate and mandate.signature else None,
            }
        )
    return {
        "circle_id": circle_id,
        "enabled": state.member_mandates_enabled(circle),
        "charter_sha256": circle.charter_sha256,
        "label": mm.MANDATE_LABEL,
        "signature_label": mm.SIGNATURE_LABEL,
        "members": members,
    }
