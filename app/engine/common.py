"""Small shared helpers for the engine: UTC time, ISO rendering, money formatting."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    """Attach UTC to naive datetimes, convert aware ones to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 UTC with a trailing Z (the wire format)."""
    if dt is None:
        return None
    return as_utc(dt).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return as_utc(datetime.fromisoformat(text))


def money(x: Decimal | str | int | float | None) -> Decimal:
    """Quantize to two places, ROUND_HALF_UP. None -> 0.00."""
    if x is None:
        return Decimal("0.00")
    if isinstance(x, float):
        x = repr(x)
    return Decimal(str(x)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money_str(x: Decimal | str | int | float | None) -> str:
    return str(money(x))


def today_of(dt: datetime) -> date:
    return as_utc(dt).date()
