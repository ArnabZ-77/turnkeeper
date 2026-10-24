"""Remedies for a short member: the charter-legal menu, set validation and the rules-mode default. Pure logic.

A remedy set is what the treasurer (the model, or rules mode) proposes after a decline. Nothing here moves
money: ``validate_remedy_set`` lists every problem a set has against the charter and the circle state, and
the engine re-checks each money step with ``app.domain.policy.PolicyEngine`` at execution time.

Rules (PLAN.md section 3 step 9 and section 5 "Guardrails"):

- every remedy type must be in ``charter.remedy_menu`` and carry the current ``charter_sha256``;
- ``buffer_advance``: amount <= buffer balance and <= one net contribution, one open advance per member, only
  for a share that is still unpaid;
- ``repay_by_invoice``: only together with a ``buffer_advance`` for the same member in the same set; the
  invoice is at most one contribution and due on a date that has not passed;
- ``turn_swap``: both members consent (checked when consents are given), at most one swap per member, only
  unpaid turns, the short member may only move LATER in the rotation (their unreceived pot is the circle's
  collateral), and the new order still satisfies the charter's collateral rule;
- ``retry_on_date``: a decline that may be retried (attempts left, never PAYER_ACTION_REQUIRED) on a future
  date at least 24 virtual hours after the last attempt;
- a set covers the round: it contains a ``buffer_advance`` or a ``retry_on_date``.
"""

from __future__ import annotations

import secrets
from collections.abc import Collection
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.charter import Charter, RemedyType, charter_sha256, collateral_violation, rounds_total, to_utc
from app.domain.fees import Money, PositiveMoney, estimate_fee, money, net_of
from app.domain.labels import display_name, fmt_money, names_of
from app.domain.policy import MAX_RETRIES, NEVER_RETRY_CODES, RETRY_SPACING, CircleState

RemedySource = Literal["agent", "rules"]
MAX_REMEDIES = 4
FRIDAY = 4  # date.weekday()
SHORT_STATUSES = frozenset({"DECLINED"})  # only a real decline is a shortfall (PENDING is not yet due)


def new_remedy_id() -> str:
    return "rm_" + secrets.token_hex(4)


def new_set_id() -> str:
    return "rs_" + secrets.token_hex(4)


class RemedyParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    swap_with: str | None = None  # turn_swap: the member whose later turn the short member takes
    amount: Money | None = None  # buffer_advance: the advance; repay_by_invoice: the invoice amount
    due_date: date | None = None  # repay_by_invoice: invoice due date; retry_on_date: the retry date


class Remedy(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=new_remedy_id)
    type: RemedyType
    member: str  # the short member
    round_n: int = Field(ge=1)
    params: RemedyParams = Field(default_factory=RemedyParams)
    reason: str = ""
    charter_sha256: str


class RemedySet(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=new_set_id)
    member: str
    round_n: int = Field(ge=1)
    remedies: list[Remedy]
    charter_sha256: str
    source: RemedySource = "rules"

    def of_type(self, kind: str) -> list[Remedy]:
        return [r for r in self.remedies if r.type == kind]

    def swap_partners(self) -> list[str]:
        return [r.params.swap_with for r in self.of_type("turn_swap") if r.params.swap_with]

    def affected_members(self) -> list[str]:
        """Whose consent the set needs: the short member when a swap is proposed, plus every swap partner."""
        partners = self.swap_partners()
        if not partners:
            return []
        out = [self.member]
        out.extend(p for p in partners if p not in out)
        return out


class Commitment(BaseModel):
    """What a member promised (``record_commitment``): pay ``amount`` on ``date``."""

    model_config = ConfigDict(frozen=True)

    member: str
    amount: PositiveMoney
    date: date
    source: RemedySource = "rules"


# --- helpers ------------------------------------------------------------------------------------------


def one_net_contribution(charter: Charter) -> Decimal:
    """What one contribution brings in after PayPal's estimated fee (the advance ceiling)."""
    return net_of(charter.amount, estimate_fee(charter.amount))


def effective_rotation(charter: Charter, state: CircleState) -> list[str]:
    return list(state.rotation_override or charter.rotation)


def swapped(rotation: list[str], a: str, b: str) -> list[str]:
    """The rotation with ``a`` and ``b`` exchanged (a new list)."""
    out = list(rotation)
    i, j = out.index(a), out.index(b)
    out[i], out[j] = out[j], out[i]
    return out


def today_of(state: CircleState) -> date | None:
    return to_utc(state.now).date() if state.now is not None else None


def next_weekday(after: date, weekday: int = FRIDAY) -> date:
    """The first ``weekday`` strictly after ``after`` (Friday by default)."""
    days = (weekday - after.weekday()) % 7 or 7
    return after + timedelta(days=days)


# --- validation ---------------------------------------------------------------------------------------


def validate_remedy_set(
    charter: Charter,
    state: CircleState,
    remedy_set: RemedySet,
    *,
    consents: Collection[str] | None = None,
) -> list[str]:
    """Every charter or policy problem with ``remedy_set`` (empty list = charter-legal).

    ``consents`` is the set of members who consented so far; ``None`` means consents are not collected yet
    and are not checked (proposal time). At execution time pass the recorded consents.
    """
    problems: list[str] = []
    handles = [m.handle for m in charter.members]
    current_hash = charter_sha256(charter)
    member = remedy_set.member
    today = today_of(state)

    if state.frozen:
        problems.append("circle is frozen: no remedy can run until it is resumed")
    if state.charter_sha256 != current_hash:
        problems.append("circle state is for a different charter version")
    if remedy_set.charter_sha256 != current_hash:
        problems.append(
            f"remedy set was proposed for charter {remedy_set.charter_sha256[:8]}, not {current_hash[:8]}"
        )
    if member not in handles:
        problems.append(f"unknown member {member!r}")
        return problems
    if not 1 <= remedy_set.round_n <= rounds_total(charter):
        problems.append(f"round {remedy_set.round_n} is out of range 1..{rounds_total(charter)}")
    if remedy_set.round_n != state.round_n:
        problems.append(f"remedy set is for round {remedy_set.round_n}, the state is round {state.round_n}")
    if not remedy_set.remedies:
        problems.append("remedy set is empty")
        return problems
    if len(remedy_set.remedies) > MAX_REMEDIES:
        problems.append(f"at most {MAX_REMEDIES} remedies per set, got {len(remedy_set.remedies)}")

    status = state.contributions.get(member, "PENDING")
    if status not in SHORT_STATUSES:
        problems.append(f"{member} is {status} for round {state.round_n}; there is no shortfall to remedy")

    seen: set[str] = set()
    for r in remedy_set.remedies:
        label = f"{r.type} for {r.member}"
        if r.type not in charter.remedy_menu:
            problems.append(
                f"{r.type} is not in the charter's remedy menu ({', '.join(charter.remedy_menu)})"
            )
        if r.charter_sha256 != current_hash:
            problems.append(f"{label}: charter hash {r.charter_sha256[:8]} does not match {current_hash[:8]}")
        if r.member != member:
            problems.append(f"{label}: every remedy in a set must be for {member}")
        if r.round_n != remedy_set.round_n:
            problems.append(f"{label}: round {r.round_n} differs from the set's round {remedy_set.round_n}")
        if r.type in seen:
            problems.append(f"{label}: at most one {r.type} per set")
        seen.add(r.type)
        if r.type == "buffer_advance":
            problems.extend(_check_advance(charter, state, r))
        elif r.type == "repay_by_invoice":
            problems.extend(_check_invoice(charter, remedy_set, r, today))
        elif r.type == "turn_swap":
            problems.extend(_check_swap(charter, state, r, consents))
        elif r.type == "retry_on_date":
            problems.extend(_check_retry(state, r, today))

    if not (seen & {"buffer_advance", "retry_on_date"}):
        problems.append("the set does not cover the shortfall: include a buffer_advance or a retry_on_date")
    return problems


def _check_advance(charter: Charter, state: CircleState, r: Remedy) -> list[str]:
    out: list[str] = []
    amount = r.params.amount
    if amount is None or amount <= 0:
        return [f"buffer_advance for {r.member} needs a positive amount"]
    if amount > state.buffer_balance:
        out.append(f"buffer advance {amount} exceeds the buffer balance {state.buffer_balance}")
    ceiling = one_net_contribution(charter)
    if amount > ceiling:
        out.append(f"buffer advance {amount} exceeds one net contribution {ceiling}")
    open_advance = state.open_advances.get(r.member)
    if open_advance:
        out.append(f"{r.member} already has an open advance of {open_advance}; at most one per member")
    return out


def _check_invoice(charter: Charter, remedy_set: RemedySet, r: Remedy, today: date | None) -> list[str]:
    out: list[str] = []
    if not [a for a in remedy_set.of_type("buffer_advance") if a.member == r.member]:
        out.append(f"repay_by_invoice for {r.member} needs a buffer_advance for {r.member} in the same set")
    amount = r.params.amount
    if amount is None or amount <= 0:
        out.append(f"repay_by_invoice for {r.member} needs a positive amount")
    elif amount > charter.amount:
        out.append(f"invoice amount {amount} exceeds one contribution {charter.amount}")
    if r.params.due_date is None:
        out.append(f"repay_by_invoice for {r.member} needs a due_date")
    elif today is not None and r.params.due_date < today:
        out.append(
            f"invoice due date {r.params.due_date.isoformat()} is in the past (today {today.isoformat()})"
        )
    return out


def _check_swap(
    charter: Charter, state: CircleState, r: Remedy, consents: Collection[str] | None
) -> list[str]:
    out: list[str] = []
    partner = r.params.swap_with
    handles = [m.handle for m in charter.members]
    if not partner:
        return [f"turn_swap for {r.member} needs swap_with"]
    if partner not in handles:
        return [f"turn_swap: unknown swap partner {partner!r}"]
    if partner == r.member:
        return [f"{r.member} cannot swap turns with themselves"]
    rotation = effective_rotation(charter, state)
    i, j = rotation.index(r.member), rotation.index(partner)
    if j <= i:
        out.append(
            f"{r.member} is short and may only move later: round {i + 1} -> round {j + 1} would move them earlier"
        )
    for handle, pos in ((r.member, i), (partner, j)):
        if pos + 1 < state.round_n:
            out.append(f"{handle}'s turn (round {pos + 1}) is already paid and cannot be swapped")
        elif pos + 1 == state.round_n and state.live_payout_exists:
            out.append(f"{handle}'s turn (round {pos + 1}) has a live payout and cannot be swapped")
    already = [h for h in (r.member, partner) if h in state.swaps_done]
    if already:
        out.append(f"{', '.join(already)} already swapped once; at most one swap per member")
    violation = collateral_violation(swapped(rotation, r.member, partner), charter.members)
    if violation is not None:
        out.append(
            f"swap would put {violation[0]} (no track record) before {violation[1]} (has a track record); "
            "the collateral rule forbids it"
        )
    if consents is not None:
        missing = [h for h in (r.member, partner) if h not in consents]
        if missing:
            out.append(
                f"turn swap needs consent from both {r.member} and {partner}; missing: {', '.join(missing)}"
            )
    return out


def _check_retry(state: CircleState, r: Remedy, today: date | None) -> list[str]:
    out: list[str] = []
    when = r.params.due_date
    if when is None:
        return [f"retry_on_date for {r.member} needs a due_date"]
    status = state.contributions.get(r.member, "PENDING")
    if status != "DECLINED":
        out.append(f"retry_on_date: {r.member} is {status}, not DECLINED")
    attempts = state.attempts.get(r.member, 0)
    if max(attempts - 1, 0) >= MAX_RETRIES:
        out.append(f"{r.member} has no retries left for round {state.round_n}")
    code = state.decline_codes.get(r.member)
    if code in NEVER_RETRY_CODES:
        out.append(f"{r.member}'s decline {code} is never retried")
    if today is not None and when <= today:
        out.append(f"retry date {when.isoformat()} must be after today ({today.isoformat()})")
    last = state.last_attempt_at.get(r.member)
    if last is not None:
        earliest = to_utc(last) + RETRY_SPACING
        if datetime.combine(when, datetime.max.time(), tzinfo=earliest.tzinfo) < earliest:
            out.append(f"retry date {when.isoformat()} is less than 24 virtual hours after the last attempt")
    return out


# --- rules-mode default ---------------------------------------------------------------------------------


def swap_partner_for(charter: Charter, state: CircleState, member: str) -> str | None:
    """The nearest later, unpaid turn the short member can legally take (None when there is none)."""
    rotation = effective_rotation(charter, state)
    if member not in rotation or member in state.swaps_done:
        return None
    i = rotation.index(member)
    if i + 1 < state.round_n:
        return None
    for j in range(i + 1, len(rotation)):
        partner = rotation[j]
        if partner in state.swaps_done:
            continue
        if collateral_violation(swapped(rotation, member, partner), charter.members) is None:
            return partner
    return None


def propose_default_remedies(
    charter: Charter, state: CircleState, member: str, commitment: Commitment | None = None
) -> RemedySet:
    """The canonical rules-mode proposal: the buffer advances the member's net share now, the member repays
    by invoice on the committed date (default: next Friday), and the member's payout turn moves later with the
    nearest member who can consent. Falls back to a retry on the committed date when the buffer cannot cover.
    """
    h = charter_sha256(charter)
    round_n = state.round_n
    today = today_of(state) or charter.start_date
    pay_date = commitment.date if commitment is not None else next_weekday(today)
    if pay_date < today:
        pay_date = next_weekday(today)
    menu = set(charter.remedy_menu)
    remedies: list[Remedy] = []
    names = names_of(charter.members)
    who = display_name(member, names)

    advance = money(min(one_net_contribution(charter), state.buffer_balance))
    can_advance = "buffer_advance" in menu and advance > 0 and not state.open_advances.get(member)
    if can_advance:
        remedies.append(
            Remedy(
                type="buffer_advance",
                member=member,
                round_n=round_n,
                params=RemedyParams(amount=advance),
                reason=(
                    f"The buffer covers {who}'s net share ({fmt_money(advance)}) now, so round {round_n} pays on"
                    " time."
                ),
                charter_sha256=h,
            )
        )
        if "repay_by_invoice" in menu:
            invoice_amount = money(charter.amount)
            remedies.append(
                Remedy(
                    type="repay_by_invoice",
                    member=member,
                    round_n=round_n,
                    params=RemedyParams(amount=invoice_amount, due_date=pay_date),
                    reason=(
                        f"{who} repays {fmt_money(invoice_amount)} by PayPal invoice due {pay_date.isoformat()}"
                        f" ({pay_date.strftime('%A')}), the payday they named; the buffer is refilled when it is paid."
                    ),
                    charter_sha256=h,
                )
            )
    elif "retry_on_date" in menu and state.decline_codes.get(member) not in NEVER_RETRY_CODES:
        # Never propose a retry the policy gates would refuse (PAYER_ACTION_REQUIRED): leave it to the organizer.
        remedies.append(
            Remedy(
                type="retry_on_date",
                member=member,
                round_n=round_n,
                params=RemedyParams(due_date=pay_date if pay_date > today else today + timedelta(days=1)),
                reason=f"The buffer cannot cover {who}; retry the charge on {pay_date.isoformat()}.",
                charter_sha256=h,
            )
        )

    partner = swap_partner_for(charter, state, member) if "turn_swap" in menu else None
    if partner is not None:
        rotation = effective_rotation(charter, state)
        i, j = rotation.index(member), rotation.index(partner)
        remedies.append(
            Remedy(
                type="turn_swap",
                member=member,
                round_n=round_n,
                params=RemedyParams(swap_with=partner),
                reason=(
                    f"{who}'s payout turn moves later (round {i + 1} -> {j + 1}) and"
                    f" {display_name(partner, names)} takes round {i + 1}, so {who}'s unreceived pot keeps"
                    " protecting the circle until the invoice is paid."
                ),
                charter_sha256=h,
            )
        )
    return RemedySet(member=member, round_n=round_n, remedies=remedies, charter_sha256=h, source="rules")
