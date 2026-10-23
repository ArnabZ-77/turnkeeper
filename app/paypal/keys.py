"""Run-scoped idempotency keys (PLAN.md section 4).

`run_id` is new on every Reset so replays never collide with an earlier run's cached PayPal answers; every retry
bumps `a{n}` and every payout re-plan bumps `r{rev}`. Max lengths are enforced here (ValueError, not `assert`,
so they survive `python -O`): PayPal-Request-Id <= 108 (Vault v3 schema; Orders/Payouts allow more),
sender_batch_id <= 30 (conservative; the Payouts schema allows 256), sender_item_id <= 63, invoice number <= 127.
"""

from __future__ import annotations

import re
import secrets

MAX_REQUEST_ID_LEN = 108
MAX_SENDER_BATCH_ID_LEN = 30
MAX_SENDER_ITEM_ID_LEN = 63
MAX_INVOICE_NUMBER_LEN = 127

RUN_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"  # lowercase RFC 4648 base32
RUN_ID_LEN = 8

_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_RUN_ID = re.compile(rf"^[{RUN_ID_ALPHABET}]{{{RUN_ID_LEN}}}$")


def _seg(value: object, label: str) -> str:
    s = str(value)
    if not _SEGMENT.match(s):
        raise ValueError(f"{label} must be non-empty and match [A-Za-z0-9_-]+, got {s!r}")
    return s


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be an int >= 1, got {value!r}")
    return value


def _fit(key: str, max_len: int, label: str) -> str:
    if len(key) > max_len:
        raise ValueError(f"{label} {key!r} is {len(key)} chars; max is {max_len}")
    return key


def order_request_id(run_id: str, circle_id: str, round_n: int, member: str, attempt: int) -> str:
    """PayPal-Request-Id for a merchant-initiated charge: "tk:{run}:{c}:{r}:{m}:a{n}"."""
    key = (
        f"tk:{_seg(run_id, 'run_id')}:{_seg(circle_id, 'circle_id')}:{_positive_int(round_n, 'round_n')}"
        f":{_seg(member, 'member')}:a{_positive_int(attempt, 'attempt')}"
    )
    return _fit(key, MAX_REQUEST_ID_LEN, "PayPal-Request-Id")


def setup_request_id(run_id: str, circle_id: str, member: str) -> str:
    """PayPal-Request-Id for a Vault setup token: "tk:{run}:{c}:setup:{m}"."""
    key = f"tk:{_seg(run_id, 'run_id')}:{_seg(circle_id, 'circle_id')}:setup:{_seg(member, 'member')}"
    return _fit(key, MAX_REQUEST_ID_LEN, "PayPal-Request-Id")


def payout_batch_id(run_id: str, circle_id: str, round_n: int, rev: int) -> str:
    """Payouts sender_batch_id: "tk:{run}:{c}:{r}:r{rev}" (PayPal dedupes it for ~30 days)."""
    key = (
        f"tk:{_seg(run_id, 'run_id')}:{_seg(circle_id, 'circle_id')}:{_positive_int(round_n, 'round_n')}"
        f":r{_positive_int(rev, 'rev')}"
    )
    return _fit(key, MAX_SENDER_BATCH_ID_LEN, "sender_batch_id")


def payout_item_id(run_id: str, circle_id: str, round_n: int, member: str) -> str:
    """Payouts sender_item_id: "{run}{c}{r}{m}" (<= 63 chars)."""
    key = f"{_seg(run_id, 'run_id')}{_seg(circle_id, 'circle_id')}{_positive_int(round_n, 'round_n')}{_seg(member, 'member')}"
    return _fit(key, MAX_SENDER_ITEM_ID_LEN, "sender_item_id")


def invoice_number(run_id: str, circle_id: str, round_n: int, member: str) -> str:
    """Invoicing v2 invoice number: "TK-{run}-{c}-{r}-{m}"."""
    key = (
        f"TK-{_seg(run_id, 'run_id')}-{_seg(circle_id, 'circle_id')}-{_positive_int(round_n, 'round_n')}"
        f"-{_seg(member, 'member')}"
    )
    return _fit(key, MAX_INVOICE_NUMBER_LEN, "invoice_number")


def new_run_id() -> str:
    """8 lowercase base32 characters from `secrets` (40 bits)."""
    return "".join(secrets.choice(RUN_ID_ALPHABET) for _ in range(RUN_ID_LEN))


def is_run_id(value: str) -> bool:
    return bool(_RUN_ID.match(value or ""))
