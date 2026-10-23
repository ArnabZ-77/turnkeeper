"""Pure functions over a ledger snapshot. Persistence (LedgerRow) belongs to the engine.

Every entry stores a positive magnitude; the ``kind`` carries the sign. Two balances are derived:

- circle_available: money the circle can pay out
    + contribution_net   a member's net capture (gross - PayPal fee)
    + buffer_advance     the buffer covers a member's share for a round (moves buffer -> circle)
    + refund             funds returned to the circle, e.g. a cancelled UNCLAIMED payout item
    - payout             pot paid to the round's recipient
    - payout_fee         PayPal's payout fee
    - capture_refund     a contribution refunded to a member (dissolve, void, canary)
- buffer_balance: the organizer's buffer
    + buffer_seed        organizer's seed
    + advance_repaid     the advanced member repaid (invoice paid, net amount)
    - buffer_advance     see above

So an advance leaves circle + buffer unchanged, and a repayment restores the buffer without touching the
circle, which has already paid the pot that the advance went into.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.fees import ZERO, NonNegativeMoney, money

LedgerKind = Literal[
    "contribution_net",
    "buffer_seed",
    "buffer_advance",
    "advance_repaid",
    "payout",
    "payout_fee",
    "refund",
    "capture_refund",
]

_CIRCLE_SIGNS: dict[str, int] = {
    "contribution_net": +1,
    "buffer_advance": +1,
    "refund": +1,
    "payout": -1,
    "payout_fee": -1,
    "capture_refund": -1,
}
_BUFFER_SIGNS: dict[str, int] = {"buffer_seed": +1, "advance_repaid": +1, "buffer_advance": -1}


class LedgerEntry(BaseModel):
    kind: LedgerKind
    amount: NonNegativeMoney  # magnitude; the kind carries the sign
    round_n: int | None = None
    member: str | None = None
    ref: str | None = None  # capture id, payout item id, invoice id, ...


class LedgerView(BaseModel):
    entries: list[LedgerEntry] = Field(default_factory=list)


def _signed_sum(view: LedgerView, signs: dict[str, int]) -> Decimal:
    return money(sum((signs[e.kind] * e.amount for e in view.entries if e.kind in signs), ZERO))


def circle_available(view: LedgerView) -> Decimal:
    """contribution_net + buffer_advance + refund - payout - payout_fee - capture_refund."""
    return _signed_sum(view, _CIRCLE_SIGNS)


def buffer_balance(view: LedgerView) -> Decimal:
    """buffer_seed + advance_repaid - buffer_advance."""
    return _signed_sum(view, _BUFFER_SIGNS)


def round_net_collected(view: LedgerView, round_n: int) -> Decimal:
    """Net contributions captured for ``round_n`` (advances are not contributions and are excluded)."""
    return money(
        sum((e.amount for e in view.entries if e.kind == "contribution_net" and e.round_n == round_n), ZERO)
    )
