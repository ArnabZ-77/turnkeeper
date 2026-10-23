"""The charter: a circle's agreed rules, their canonical JSON form and SHA-256 hash. Pure logic.

A charter is immutable once agreed (the models are frozen): any change is a new charter with a new hash and
fresh member consent. ``canonical_json`` is the only serialization used for hashing and for the mandate
HMAC, so its rules are fixed: sorted keys, compact separators, Decimal as two-place strings, dates as ISO
strings, no whitespace.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.fees import NonNegativeMoney, PositiveMoney, wire_amount

RemedyType = Literal["retry_on_date", "turn_swap", "buffer_advance", "repay_by_invoice"]
FeePolicy = Literal["net_pot", "gross_up", "organizer_absorbs"]
Wallet = Literal["PAYPAL", "VENMO"]
Cadence = Literal["monthly", "weekly"]

ALL_REMEDIES: tuple[RemedyType, ...] = ("retry_on_date", "turn_swap", "buffer_advance", "repay_by_invoice")
HANDLE_PATTERN = r"^[a-z][a-z0-9_]{1,15}$"
QUORUM_PATTERN = r"^[0-9]+/[0-9]+$"


class Member(BaseModel):
    model_config = ConfigDict(frozen=True)

    handle: str = Field(pattern=HANDLE_PATTERN, description="logical handle, unique in a charter")
    name: str = Field(min_length=1)
    lang: str = "en"
    email: str | None = None
    payday_rule: str | None = None  # free text, e.g. "every 2nd Friday"
    payout_wallet: Wallet = "PAYPAL"
    payout_receiver: str | None = None  # email (PAYPAL) or E.164 phone (VENMO)
    track_record_rounds: int = Field(default=0, ge=0)  # completed rounds in earlier circles; 0 = none


class Buffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed: NonNegativeMoney = Decimal("0.00")
    source: Literal["organizer"] = "organizer"
    retention_pct: int = Field(default=0, ge=0, le=100)


class Charter(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    currency: Literal["USD"] = "USD"
    amount: PositiveMoney
    cadence: Cadence = "monthly"
    start_date: date
    members: list[Member] = Field(min_length=1)
    rotation: list[str]  # member handles in payout order
    fee_policy: FeePolicy = "net_pot"
    buffer: Buffer = Field(default_factory=Buffer)
    grace_days: int = Field(default=3, ge=0)
    quorum: str = Field(default="4/6", pattern=QUORUM_PATTERN)  # "k/n"
    remedy_menu: list[RemedyType] = Field(default_factory=lambda: list(ALL_REMEDIES))
    languages: list[str] = Field(default_factory=lambda: ["en"])

    @model_validator(mode="after")
    def _check_rules(self) -> Charter:
        handles = [m.handle for m in self.members]
        dupes = sorted({h for h in handles if handles.count(h) > 1})
        if dupes:
            raise ValueError(f"duplicate member handles: {', '.join(dupes)}")
        if sorted(self.rotation) != sorted(handles):
            raise ValueError(
                f"rotation {self.rotation} must be a permutation of the member handles {sorted(handles)}"
            )
        violation = collateral_violation(self.rotation, self.members)
        if violation is not None:
            untracked, tracked = violation
            raise ValueError(
                f"collateral rule: {untracked} has no track record but is scheduled before {tracked}, "
                "who has one; members without a track record receive their pot last"
            )
        k, n = _parse_quorum(self.quorum)
        if n != len(self.members):
            raise ValueError(f"quorum {self.quorum}: n must equal the member count {len(self.members)}")
        if not 1 <= k <= n:
            raise ValueError(f"quorum {self.quorum}: k must be between 1 and {n}")
        return self


def collateral_violation(rotation: Sequence[str], members: Iterable[Member]) -> tuple[str, str] | None:
    """The first ``(untracked, tracked)`` pair where a member with no track record precedes one who has one.

    A member's unreceived pot is the circle's collateral against them stopping payments after an early
    payout, so members with ``track_record_rounds == 0`` must come after everyone with a track record.
    Returns None when the order is fine.
    """
    track = {m.handle: m.track_record_rounds for m in members}
    first_untracked: str | None = None
    for handle in rotation:
        if track.get(handle, 0) > 0:
            if first_untracked is not None:
                return first_untracked, handle
        elif first_untracked is None:
            first_untracked = handle
    return None


def _parse_quorum(quorum: str) -> tuple[int, int]:
    k, _, n = quorum.partition("/")
    return int(k), int(n)


# --- canonical form and hash ----------------------------------------------------------------------------


def to_utc(dt: datetime) -> datetime:
    """Naive datetimes are taken as UTC; aware ones are converted to UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _canon(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        raise TypeError("float is not allowed in canonical JSON; use Decimal")
    if isinstance(value, Decimal):
        return wire_amount(value)
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return to_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canon(value.value)
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canon(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted((_canon(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical_dumps(obj: Any) -> str:
    """Deterministic JSON: sorted keys, ``(",", ":")`` separators, Decimal as ``"200.00"``, dates ISO-8601,
    datetimes ISO-8601 in UTC, sets sorted. Used for the charter hash and the mandate signature."""
    return json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json(charter: Charter) -> str:
    """The charter's canonical JSON (sorted keys, Decimal -> str, dates ISO, no whitespace)."""
    return canonical_dumps(charter)


def charter_sha256(charter: Charter) -> str:
    """Hex SHA-256 of ``canonical_json(charter)`` encoded as UTF-8."""
    return hashlib.sha256(canonical_json(charter).encode("utf-8")).hexdigest()


# --- derived facts --------------------------------------------------------------------------------------


def quorum_tuple(charter: Charter) -> tuple[int, int]:
    """``"4/6" -> (4, 6)``."""
    return _parse_quorum(charter.quorum)


def rounds_total(charter: Charter) -> int:
    """One round per member."""
    return len(charter.members)


def _check_round(charter: Charter, round_n: int) -> None:
    total = rounds_total(charter)
    if not 1 <= round_n <= total:
        raise ValueError(f"round {round_n} is out of range 1..{total}")


def recipient_for_round(charter: Charter, round_n: int) -> str:
    """Handle of the member who receives the pot in ``round_n`` (1-based)."""
    _check_round(charter, round_n)
    return charter.rotation[round_n - 1]


def add_months(d: date, months: int) -> date:
    """Calendar month arithmetic with the day clamped to the target month (Jan 31 + 1 -> Feb 28)."""
    index = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def due_date(charter: Charter, round_n: int) -> date:
    """When round ``round_n`` is collected: ``start_date`` plus ``round_n - 1`` months or weeks."""
    _check_round(charter, round_n)
    if charter.cadence == "weekly":
        return charter.start_date + timedelta(weeks=round_n - 1)
    return add_months(charter.start_date, round_n - 1)
