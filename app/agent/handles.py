"""Logical handles: what the model sees instead of raw PayPal IDs.

Tools take and return handles such as ``round_n=3``, ``member="ravi"`` and ``capture_ref="cap:ravi:r3"``.
Receipts written by the model may contain placeholders such as ``{capture_id:asha:r3}`` which the server
fills in from the ledger. Keeping run-specific IDs out of prompts makes replays session-independent and
keeps PII out of the model's context.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

HANDLE_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
CIRCLE_ID_RE = re.compile(r"^[a-z0-9]{6}$")
_REF_RE = re.compile(r"^(?P<kind>cap|ord|pay|inv):(?P<member>[a-z][a-z0-9_]{1,15}):r(?P<round>\d{1,3})$")
_PLACEHOLDER_RE = re.compile(
    r"\{(?P<kind>capture_id|order_id|payout_item_id|batch_id|invoice_id):(?P<member>[a-z][a-z0-9_]{1,15}):r(?P<round>\d{1,3})\}"
)

RefKind = Literal["cap", "ord", "pay", "inv"]
_PLACEHOLDER_TO_KIND: dict[str, RefKind] = {
    "capture_id": "cap",
    "order_id": "ord",
    "payout_item_id": "pay",
    "batch_id": "pay",
    "invoice_id": "inv",
}


def is_handle(value: object) -> bool:
    """True when ``value`` is a valid member handle (``^[a-z][a-z0-9_]{1,15}$``)."""
    return isinstance(value, str) and bool(HANDLE_RE.match(value))


def is_circle_id(value: object) -> bool:
    """True when ``value`` is a 6-char lowercase alphanumeric circle id."""
    return isinstance(value, str) and bool(CIRCLE_ID_RE.match(value))


def is_round_n(value: object) -> bool:
    """True for a positive int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def validate_inputs(*, circle_id: object = None, member: object = None, round_n: object = None) -> str | None:
    """Return a human-readable problem with the given handles, or None when they are all well-formed.

    Only the arguments that are passed (not None) are checked.
    """
    if circle_id is not None and not is_circle_id(circle_id):
        return f"invalid circle_id {circle_id!r}: expected 6 lowercase letters or digits"
    if member is not None and not is_handle(member):
        return f"invalid member handle {member!r}: expected ^[a-z][a-z0-9_]{{1,15}}$"
    if round_n is not None and not is_round_n(round_n):
        return f"invalid round_n {round_n!r}: expected a positive integer"
    return None


def make_ref(kind: RefKind, member: str, round_n: int) -> str:
    """Build a logical reference such as ``cap:ravi:r3``."""
    if not is_handle(member) or not is_round_n(round_n):
        raise ValueError(f"bad handle for ref: member={member!r} round_n={round_n!r}")
    return f"{kind}:{member}:r{round_n}"


def capture_ref(member: str, round_n: int) -> str:
    return make_ref("cap", member, round_n)


def order_ref(member: str, round_n: int) -> str:
    return make_ref("ord", member, round_n)


def payout_ref(member: str, round_n: int) -> str:
    return make_ref("pay", member, round_n)


def parse_ref(ref: str) -> tuple[RefKind, str, int]:
    """Parse ``cap:ravi:r3`` into ``("cap", "ravi", 3)``. Raises ValueError on anything else."""
    m = _REF_RE.match(ref or "")
    if not m:
        raise ValueError(f"not a logical reference: {ref!r}")
    kind = m.group("kind")
    return kind, m.group("member"), int(m.group("round"))  # type: ignore[return-value]


def fill_placeholders(text: str, lookup: Callable[[RefKind, str, int], str | None]) -> str:
    """Replace ``{capture_id:asha:r3}``-style placeholders using ``lookup(kind, member, round_n)``.

    Unknown placeholders (lookup returns None) are left untouched so a reader can see what is missing.
    """

    def _sub(m: re.Match[str]) -> str:
        kind = _PLACEHOLDER_TO_KIND[m.group("kind")]
        value = lookup(kind, m.group("member"), int(m.group("round")))
        return value if value else m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text or "")


def first_name(name: str | None) -> str:
    """Reduce a display name to its first token (PII reduction for the public surface)."""
    if not name:
        return ""
    return name.strip().split()[0]
