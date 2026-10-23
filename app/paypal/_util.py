"""Small helpers shared by the typed wrappers: money formatting and HATEOAS link lookup."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

CENTS = Decimal("0.01")


def to_decimal(value: Any) -> Decimal | None:
    """Parse a PayPal money value (`{"value": "7.47", ...}` or a bare string/number) into a 2-place Decimal.

    Returns None when the value is missing or unparsable. Never uses float arithmetic.
    """
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def money_str(amount: Decimal | str | int, *, positive: bool = True) -> str:
    """Format an amount for the wire: exactly two decimal places, e.g. Decimal("200") -> "200.00"."""
    if isinstance(amount, float):
        raise TypeError("money must be Decimal/str/int, never float")
    try:
        dec = Decimal(str(amount)).quantize(CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"invalid money amount: {amount!r}") from e
    if positive and dec <= 0:
        raise ValueError(f"amount must be positive: {amount!r}")
    return f"{dec:.2f}"


def find_link(data: dict | None, *rels: str) -> str | None:
    """Return the href of the first HATEOAS link whose `rel` is in `rels` (checked in list order)."""
    if not isinstance(data, dict):
        return None
    for link in data.get("links") or []:
        if isinstance(link, dict) and link.get("rel") in rels and link.get("href"):
            return str(link["href"])
    return None


def check_len(value: str, max_len: int, label: str) -> str:
    """Raise ValueError when `value` exceeds PayPal's documented maximum length for `label`."""
    if len(value) > max_len:
        raise ValueError(f"{label} is {len(value)} chars; PayPal allows at most {max_len}")
    return value
