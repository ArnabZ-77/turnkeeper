"""User-facing wording for internal identifiers (pure): remedy kinds, member handles and money amounts.

Toasts, story summaries and remedy reasons are read by people, so they say "buffer advance" (not
``buffer_advance``), "Ravi Sharma" (not ``ravi``) and "$192.53" (not ``192.53``). Traces and logs keep the
identifiers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

REMEDY_LABEL: dict[str, str] = {
    "buffer_advance": "buffer advance",
    "repay_by_invoice": "repay by PayPal invoice",
    "turn_swap": "turn swap",
    "retry_on_date": "dated retry",
    "retry_on": "dated retry",
}


def remedy_label(kind: Any) -> str:
    text = str(kind or "")
    return REMEDY_LABEL.get(text, text.replace("_", " "))


def remedy_list(kinds: Iterable[Any]) -> str:
    return ", ".join(remedy_label(k) for k in kinds if k) or "none"


def display_name(handle: Any, names: Mapping[str, str] | None = None) -> str:
    """The member's full name when known, else the handle in title case ("judge_member" -> "Judge Member")."""
    h = str(handle or "")
    if names and names.get(h):
        return str(names[h])
    return h.replace("_", " ").title() if h else h


def names_of(members: Iterable[Any]) -> dict[str, str]:
    """{handle: name} from charter members or member rows (anything with .handle and .name)."""
    return {str(m.handle): str(m.name) for m in members if getattr(m, "name", None)}


def fmt_money(amount: Any) -> str:
    """'$192.53' (two decimals); a value that is not a number comes back unchanged."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return str(amount)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"
