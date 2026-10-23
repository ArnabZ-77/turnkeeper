"""Money arithmetic: quantization, PayPal fee estimates and pot math. Pure functions, no I/O.

Every amount in Turnkeeper is a ``Decimal`` with exactly two places. ``money`` is the one place where
rounding happens (ROUND_HALF_UP, the convention PayPal uses on its own fee lines); ``wire_amount`` is the
``"200.00"`` string form used on the wire and in the database.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator, BeforeValidator

if TYPE_CHECKING:  # charter.py imports the Money types from here; keep the runtime import one-way
    from app.domain.charter import Charter

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# PayPal US commercial rate: 3.49% + $0.49 per transaction (the D1 smoke test records the sandbox figure).
DEFAULT_FEE_PCT = Decimal("0.0349")
DEFAULT_FEE_FIXED = Decimal("0.49")

MoneyInput = Decimal | int | str


def _to_decimal(x: object) -> Decimal:
    """Exact conversion to Decimal without rounding. Floats go through their shortest repr as a last resort."""
    if isinstance(x, bool):
        raise ValueError("money must not be a bool")
    if isinstance(x, Decimal):
        d = x
    elif isinstance(x, float):
        d = Decimal(repr(x))
    elif isinstance(x, int | str):
        try:
            d = Decimal(x)
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"not a money value: {x!r}") from e
    else:
        raise ValueError(f"not a money value: {x!r}")
    if not d.is_finite():
        raise ValueError(f"not a finite money value: {x!r}")
    return d


def money(x: MoneyInput) -> Decimal:
    """Quantize ``x`` to two places with ROUND_HALF_UP ("0.005" -> 0.01, "1.004" -> 1.00).

    Accepts Decimal, int or a numeric string. Raises ValueError for NaN, infinities and junk.
    """
    return _to_decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def wire_amount(x: MoneyInput) -> str:
    """The on-the-wire form, always two places and never scientific notation: ``"200.00"``."""
    return format(money(x), "f")


# --- pydantic field types -------------------------------------------------------------------------------


def _validate_two_places(v: Decimal) -> Decimal:
    if not v.is_finite():
        raise ValueError("money must be finite")
    q = v.quantize(CENT, rounding=ROUND_HALF_UP)
    if q != v:
        raise ValueError(f"money must have at most 2 decimal places, got {v}")
    return q


def _validate_non_negative(v: Decimal) -> Decimal:
    if v < 0:
        raise ValueError(f"money must not be negative, got {v}")
    return v


def _validate_positive(v: Decimal) -> Decimal:
    if v <= 0:
        raise ValueError(f"money must be positive, got {v}")
    return v


# A Decimal with at most two decimal places, normalized to exactly two ("200" -> 200.00; "200.005" rejected).
Money = Annotated[Decimal, BeforeValidator(_to_decimal), AfterValidator(_validate_two_places)]
NonNegativeMoney = Annotated[Money, AfterValidator(_validate_non_negative)]
PositiveMoney = Annotated[Money, AfterValidator(_validate_positive)]


# --- fee and pot math -----------------------------------------------------------------------------------


def estimate_fee(
    gross: MoneyInput, pct: Decimal = DEFAULT_FEE_PCT, fixed: Decimal = DEFAULT_FEE_FIXED
) -> Decimal:
    """Estimated PayPal fee on a capture of ``gross``: ``gross * pct + fixed``, rounded half up.

    A zero capture has no fee. The real fee comes from ``seller_receivable_breakdown`` once the capture
    exists; this estimate sizes reserves and advances before that.
    """
    g = money(gross)
    if g < 0:
        raise ValueError(f"gross must not be negative, got {g}")
    if g == 0:
        return ZERO
    return money(g * _to_decimal(pct) + _to_decimal(fixed))


def net_of(gross: MoneyInput, fee: MoneyInput) -> Decimal:
    """What the circle actually receives from a capture: ``gross - fee``."""
    g, f = money(gross), money(fee)
    if f < 0:
        raise ValueError(f"fee must not be negative, got {f}")
    if f > g:
        raise ValueError(f"fee {f} exceeds gross {g}")
    return money(g - f)


def pot_amount(
    charter: Charter,
    net_contributions: Iterable[MoneyInput],
    advance_net: MoneyInput,
    payout_fee_reserve: MoneyInput,
) -> Decimal:
    """The amount the round's recipient is paid, by the charter's fee policy. Never negative.

    - ``net_pot``: sum of the net contributions + approved buffer advance - payout fee reserve.
    - ``gross_up`` / ``organizer_absorbs``: the recipient receives the gross value of the contributions
      (``charter.amount`` x their number) + the advance; the fees and the reserve are borne by the
      grossed-up charges or by the organizer's buffer, not by the pot.
    """
    nets = [money(n) for n in net_contributions]
    if any(n < 0 for n in nets):
        raise ValueError("net contributions must not be negative")
    advance, reserve = money(advance_net), money(payout_fee_reserve)
    if advance < 0 or reserve < 0:
        raise ValueError("advance and reserve must not be negative")
    if charter.fee_policy == "net_pot":
        total = sum(nets, ZERO) + advance - reserve
    else:
        total = money(charter.amount) * len(nets) + advance
    return money(max(total, ZERO))
