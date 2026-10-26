"""Context assembly for a model that proposes remedies: the charter's preference order and computed facts.

A small model asked to "propose a charter-legal remedy set" from raw state picks the cheapest legal option
(a lone retry) and never works out a legal turn swap: it would have to apply the rotation order, the paid
turns, the collateral rule, consents and earlier swaps itself. So the deterministic side does that arithmetic
and hands the model the results, and the guide (``prompts/remedies.md``) states the preference order the
charter's default proposal follows. The model still chooses the set and reads the member's reply; the
validator and the policy engine still check everything it proposes.

Pure: no I/O beyond reading the guide once, no money moves.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.charter import Charter, collateral_violation, to_utc
from app.domain.fees import money
from app.domain.policy import MAX_RETRIES, NEVER_RETRY_CODES, RETRY_SPACING, CircleState
from app.domain.remedies import (
    effective_rotation,
    next_weekday,
    one_net_contribution,
    swapped,
    today_of,
)

GUIDE_PATH = Path(__file__).parent / "prompts" / "remedies.md"
CALENDAR_DAYS = 14
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# Hindi weekday names in Devanagari and the usual Latin spellings (replies mix both).
WEEKDAYS_HI = ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार")
WEEKDAYS_HI_LATIN = ("somvar", "mangalvar", "budhvar", "guruvar", "shukravar", "shanivar", "ravivar")


@lru_cache(maxsize=1)
def remedy_guide() -> str:
    """The remedy preference order and output rules (shared by the treasurer prompt docs and the eval)."""
    return GUIDE_PATH.read_text(encoding="utf-8").strip()


def calendar(today: date, days: int = CALENDAR_DAYS) -> list[dict[str, str]]:
    """The next ``days`` dates after ``today`` with weekday names in English and Hindi (both scripts)."""
    out = []
    for k in range(1, days + 1):
        d = today + timedelta(days=k)
        w = d.weekday()
        out.append(
            {
                "date": d.isoformat(),
                "weekday": WEEKDAYS_EN[w],
                "hi": WEEKDAYS_HI[w],
                "hi_latin": WEEKDAYS_HI_LATIN[w],
            }
        )
    return out


def advance_facts(charter: Charter, state: CircleState, member: str) -> dict[str, Any]:
    """Whether the buffer may advance the member's share now, and the largest legal amount."""
    ceiling = one_net_contribution(charter)
    largest = money(min(ceiling, state.buffer_balance))
    open_advance = state.open_advances.get(member)
    why_not = None
    if "buffer_advance" not in charter.remedy_menu:
        why_not = "buffer_advance is not in the charter's remedy menu"
    elif open_advance:
        why_not = f"{member} already has an open advance of {open_advance}; at most one per member"
    elif largest <= 0:
        why_not = "the buffer is empty"
    return {
        "allowed": why_not is None,
        "max_amount": str(largest) if why_not is None else "0.00",
        "why_not": why_not,
        "limits": f"at most the buffer balance ({state.buffer_balance}) and one net contribution ({ceiling})",
    }


def retry_facts(charter: Charter, state: CircleState, member: str, today: date) -> dict[str, Any]:
    """Whether the declined charge may be retried, how often, and the earliest legal retry date."""
    attempts = state.attempts.get(member, 0)
    left = max(MAX_RETRIES - max(attempts - 1, 0), 0)
    code = state.decline_codes.get(member)
    earliest = today + timedelta(days=1)
    last = state.last_attempt_at.get(member)
    if last is not None:
        earliest = max(earliest, (to_utc(last) + RETRY_SPACING).date())
    why_not = None
    if "retry_on_date" not in charter.remedy_menu:
        why_not = "retry_on_date is not in the charter's remedy menu"
    elif code in NEVER_RETRY_CODES:
        why_not = f"decline code {code} is never retried: the member must act in PayPal first"
    elif left <= 0:
        why_not = f"no retries left this round ({MAX_RETRIES} used)"
    return {
        "allowed": why_not is None,
        "retries_left": left,
        "earliest_date": earliest.isoformat(),
        "why_not": why_not,
    }


def legal_swap_partners(
    charter: Charter, state: CircleState, member: str, refused: Collection[str] = ()
) -> tuple[list[str], str | None]:
    """Every member whose later, unpaid turn the short member may take, nearest first, and why there is none.

    Mirrors ``remedies._check_swap``: the short member only moves later, both turns are unpaid (and not the
    current round once its payout is live), nobody swaps twice, the new order keeps the collateral rule, and
    nobody listed in ``refused`` takes part.
    """
    if "turn_swap" not in charter.remedy_menu:
        return [], "turn_swap is not in the charter's remedy menu"
    rotation = effective_rotation(charter, state)
    if member not in rotation:
        return [], f"{member} is not in the rotation"
    if member in refused:
        return [], f"{member} refused to move their turn"
    if member in state.swaps_done:
        return [], f"{member} already swapped once this circle"
    i = rotation.index(member)

    def unpaid(pos: int) -> bool:
        return pos + 1 > state.round_n or (pos + 1 == state.round_n and not state.live_payout_exists)

    if not unpaid(i):
        return [], f"{member}'s turn (round {i + 1}) is already paid"
    later = rotation[i + 1 :]
    if not later:
        return [], f"{member} already has the last turn; there is no later turn to move to"
    partners, reasons = [], []
    for partner in later:
        if partner in refused:
            reasons.append(f"{partner} refused")
        elif partner in state.swaps_done:
            reasons.append(f"{partner} already swapped once")
        elif collateral_violation(swapped(rotation, member, partner), charter.members) is not None:
            reasons.append(f"swapping with {partner} would break the collateral rule")
        else:
            partners.append(partner)
    return partners, (None if partners else "; ".join(reasons))


def remedy_facts(
    charter: Charter,
    state: CircleState,
    member: str,
    *,
    refused: Collection[str] = (),
    member_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything a model needs to choose a remedy set for ``member``, with the legality already worked out."""
    today = today_of(state) or charter.start_date
    rotation = effective_rotation(charter, state)
    tracks = {m.handle: m.track_record_rounds for m in charter.members}
    partners, swap_why_not = legal_swap_partners(charter, state, member, refused)
    return {
        "today": today.isoformat(),
        "today_weekday": WEEKDAYS_EN[today.weekday()],
        "round": state.round_n,
        "member": member,
        "decline_code": state.decline_codes.get(member),
        "contribution": str(charter.amount),
        "one_net_contribution": str(one_net_contribution(charter)),
        "buffer_balance": str(state.buffer_balance),
        "open_advance": str(state.open_advances.get(member) or "0.00"),
        "remedy_menu": list(charter.remedy_menu),
        "buffer_advance": advance_facts(charter, state, member),
        "retry_on_date": retry_facts(charter, state, member, today),
        "turn_swap": {
            "legal_partners": partners,
            "why_none": swap_why_not,
        },
        "default_due_date": next_weekday(today).isoformat(),
        "rotation": [
            {
                "round": k + 1,
                "member": h,
                "track_record_rounds": tracks.get(h, 0),
                "paid": k + 1 < state.round_n,
            }
            for k, h in enumerate(rotation)
        ],
        "refused": sorted(refused),
        "member_history": member_history or {},
        "calendar": calendar(today),
    }
