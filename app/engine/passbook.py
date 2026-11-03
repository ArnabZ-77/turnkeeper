"""Member Passbook: one member's own view of the circle, built only from stored state (flag ``TK_PASSBOOK``).

Opened with a member capability (``app.domain.capability``, read or answer scope). Nothing here calls PayPal or a
model. Sections: my payments (each with its PayPal capture id and a receipt), this round, my turn, my fees, and
"if someone misses" from the frozen charter.

Dignity rule: another member's shortfall is never named. A viewer sees counts ("5 of 6 paid, 1 covered by the
circle buffer"); only the short member sees that it was them.

Each capture's receipt is the chain of authority behind it, in order, from stored rows only: the Vault approval,
the charter hash, the fee shown at join, the member mandate (server-attested), the organizer's payout mandate,
for a covered share the agent's rule, the remedy-set hash and the organizer's approval, then the
``PayPal-Request-Id`` and the capture id. A link that has no stored value is left out, never invented.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlmodel import select

from app.db import session_scope
from app.domain import capability as capm
from app.domain.charter import charter_sha256, quorum_tuple
from app.domain.fees import estimate_fee, net_of
from app.engine import capabilities, member_mandates, recovery, state
from app.engine.common import iso
from app.models import Contribution, MandateRow, RemedySetRow, Round, Vote

PAID = ("COMPLETED", "COVERED")


def enabled() -> bool:
    import os

    return (os.environ.get("TK_PASSBOOK") or "").strip().lower() in ("1", "true", "on", "yes")


def _organizer_mandate(circle_id: str) -> dict[str, Any] | None:
    with session_scope() as db:
        row = db.exec(
            select(MandateRow).where(MandateRow.circle_id == circle_id).order_by(MandateRow.id.desc())
        ).first()  # type: ignore[union-attr]
    if row is None:
        return None
    try:
        data = json.loads(row.json)
    except ValueError:
        return None
    sig = str(data.get("signature") or "")
    return {"signature": sig[:12] + "…" if sig else None, "approved_at": iso(row.approved_at)}


def _covered_chain(circle_id: str, member: str, round_n: int) -> list[dict[str, Any]]:
    """Remedy links for a share the circle covered: the agent's rule, the set hash, the organizer's approval."""
    with session_scope() as db:
        rs = db.exec(
            select(RemedySetRow)
            .where(
                RemedySetRow.circle_id == circle_id,
                RemedySetRow.member == member,
                RemedySetRow.round_n == round_n,
            )
            .where(RemedySetRow.status.in_(("executed", "repaid", "approved")))  # type: ignore[attr-defined]
            .order_by(RemedySetRow.created_at.desc())  # type: ignore[attr-defined]
        ).first()
        votes = db.exec(select(Vote).where(Vote.set_id == rs.id)).all() if rs else []
    if rs is None:
        return []
    out = [
        {
            "step": "remedy_proposed",
            "label": f"Remedy proposed by {'the AI treasurer' if rs.source == 'agent' else 'the rules'}",
            "value": rs.id,
        },
        {"step": "remedy_set_sha", "label": "Remedy set hash", "value": capm.set_digest(rs.json)[:12] + "…"},
    ]
    agent_votes = [v for v in votes if v.source == "agent"]
    if agent_votes:
        out.append(
            {
                "step": "agent_rule",
                "label": "Member agents answered inside their mandates",
                "value": f"{len(agent_votes)} answers",
            }
        )
    if rs.approved_at:
        out.append(
            {
                "step": "organizer_approval",
                "label": f"Approved by {rs.approved_by or 'the organizer'}",
                "value": iso(rs.approved_at),
            }
        )
    return out


def _receipt(
    circle_id: str,
    c: Contribution,
    mrow: Any,
    charter: Any,
    sha: str,
    mandate: dict | None,
    mm_view: dict | None,
) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    if mrow is not None and mrow.joined_at:
        chain.append(
            {
                "step": "vault_approval",
                "label": "Approved once on PayPal (Vault, recurring)",
                "value": iso(mrow.joined_at),
            }
        )
    chain.append({"step": "charter", "label": "Frozen charter", "value": sha[:12] + "…"})
    fee = estimate_fee(charter.amount)
    chain.append(
        {"step": "fees_at_join", "label": "Fee shown before approval", "value": f"{fee} of {charter.amount}"}
    )
    if mm_view is not None:
        chain.append(
            {
                "step": "member_mandate",
                "label": "Your mandate (server-attested)",
                "value": mm_view.get("signature"),
            }
        )
    if mandate is not None and mandate.get("signature"):
        chain.append(
            {
                "step": "organizer_mandate",
                "label": "Organizer's payout mandate",
                "value": mandate["signature"],
            }
        )
    if c.status == "COVERED":
        chain.extend(_covered_chain(circle_id, c.member_handle, c.round_n))
    else:
        chain.append(
            {
                "step": "policy",
                "label": "Policy check passed before PayPal was called",
                "value": "app/domain/policy.py",
            }
        )
    if c.request_id:
        chain.append({"step": "request_id", "label": "PayPal-Request-Id", "value": c.request_id})
    if c.capture_id:
        chain.append({"step": "capture", "label": "PayPal capture", "value": c.capture_id})
    return chain


def view(token: str) -> dict[str, Any]:
    cap = capabilities.open_token(token)
    circle = state.load_circle(cap.circle_id)
    if not circle.is_fixture_clone:
        raise capm.CapabilityError(capm.NOT_A_DEMO_CIRCLE, "the passbook is for demo circles only")
    charter = state.charter_of(circle)
    sha = charter_sha256(charter)
    me = cap.member
    members = {m.handle: m for m in state.members_of(circle.id)}
    mrow = members.get(me)
    with session_scope() as db:
        contribs = db.exec(
            select(Contribution).where(Contribution.circle_id == circle.id).order_by(Contribution.round_n)
        ).all()
        rounds = db.exec(select(Round).where(Round.circle_id == circle.id).order_by(Round.n)).all()
    mandate = _organizer_mandate(circle.id)
    mm_view = None
    if member_mandates.enabled(circle.id):
        m = member_mandates.load_mandate(circle.id, me)
        if m is not None and m.signature:
            mm_view = {"signature": m.signature[:12] + "…"}

    mine = [c for c in contribs if c.member_handle == me]
    payments = [
        {
            "round_n": c.round_n,
            "status": c.status,
            "gross": c.gross,
            "fee": c.fee,
            "net": c.net,
            "capture_id": c.capture_id,
            "label": c.label,
            "receipt": _receipt(circle.id, c, mrow, charter, sha, mandate, mm_view),
        }
        for c in mine
        if c.status in PAID or c.status == "DECLINED"
    ]

    # this round: the latest round with any settled contribution
    settled = [c for c in contribs if c.status in PAID or c.status == "DECLINED"]
    this_round = None
    if settled:
        n = max(c.round_n for c in settled)
        row = [c for c in contribs if c.round_n == n]
        covered = [c for c in row if c.status == "COVERED"]
        mine_now = next((c for c in row if c.member_handle == me), None)
        this_round = {
            "round_n": n,
            "paid": sum(1 for c in row if c.status == "COMPLETED"),
            "total": len(charter.members),
            "covered_by_buffer": len(covered),
            "mine": mine_now.status if mine_now else None,  # only my own status; nobody else is named
        }

    # my turn: the current rotation (after any swap) vs the charter's original order
    original = charter.rotation.index(me) + 1 if me in charter.rotation else None
    current = mrow.rotation_pos if mrow is not None else original
    my_round = next((r for r in rounds if r.recipient_handle == me), None)
    turn = {
        "original_round": original,
        "round_n": my_round.n if my_round else current,
        "moved": bool(original and my_round and my_round.n != original),
        "payout_status": my_round.payout_status if my_round else None,
        "payout_amount": my_round.payout_amount if my_round else None,
        "payout_batch_id": my_round.payout_batch_id if my_round else None,
        "due": iso(my_round.due_at) if my_round else None,
    }
    from app.engine.rounds import PAYOUT_FEE_RESERVE

    # every net contribution, less the Payouts fee the pot carries (fee_policy net_pot)
    expected_pot = (
        net_of(charter.amount, estimate_fee(charter.amount)) * len(charter.members) - PAYOUT_FEE_RESERVE
    )

    fees_paid = sum((Decimal(c.fee) for c in mine if c.fee and c.status in PAID), Decimal("0.00"))
    k, n_q = quorum_tuple(charter)
    repayment = None
    inv = recovery.current_invoice(circle.id)
    if inv is not None:
        remedy = recovery.rd.Remedy.model_validate_json(inv.json)
        if remedy.member == me:  # only the member who owes it sees the invoice
            repayment = {
                "invoice_id": inv.invoice_id,
                "status": inv.invoice_status,
                "amount": str(remedy.params.amount) if remedy.params.amount else None,
                "due_date": remedy.params.due_date.isoformat() if remedy.params.due_date else None,
                "qr": bool(inv.invoice_qr),
                "simulated": bool(inv.invoice_simulated),
            }
    return {
        "ok": True,
        "label": "demo member view, no login",
        "member": {"handle": me, "name": (mrow.name if mrow else me), "lang": (mrow.lang if mrow else "en")},
        "circle": {
            "name": charter.name,
            "currency": charter.currency,
            "amount": str(charter.amount),
            "cadence": charter.cadence,
            "rounds": len(charter.members),
            "charter_sha256": sha,
        },
        "payments": payments,
        "this_round": this_round,
        "turn": {**turn, "expected_pot": str(expected_pot)},
        "fees": {
            "paid": str(fees_paid),
            "per_payment": str(estimate_fee(charter.amount)),
            "policy": charter.fee_policy,
        },
        "if_someone_misses": {
            "buffer": str(charter.buffer.seed),
            "remedy_menu": list(charter.remedy_menu),
            "quorum": f"{k} of {n_q}",
            "grace_days": charter.grace_days,
            "charter_sha256": sha,
        },
        "repayment": repayment,
        "expires_at": cap.exp,
    }


def qr_png(token: str) -> bytes | None:
    """The member's own repayment QR code (PNG bytes), or None."""
    import base64

    cap = capabilities.open_token(token)
    inv = recovery.current_invoice(cap.circle_id)
    if inv is None or not inv.invoice_qr or "," not in inv.invoice_qr:
        return None
    if recovery.rd.Remedy.model_validate_json(inv.json).member != cap.member:
        return None
    head, data = inv.invoice_qr.split(",", 1)
    if not head.startswith("data:image/png"):
        return None
    return base64.b64decode(data)
