"""Virtual clock per circle, persisted on Circle.virtual_now.

Judge Mode advances it with buttons; due dates, retry spacing and mandate expiry read from it.
Wall-clock timestamps (ticker, received_at) deliberately stay real.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.db import session_scope
from app.engine.common import as_utc, iso
from app.models import Circle


class VirtualClock:
    def __init__(self, circle: Circle) -> None:
        self.circle = circle

    def now(self) -> datetime:
        return as_utc(self.circle.virtual_now)

    def today(self) -> date:
        return self.now().date()

    def advance(self, days: float) -> datetime:
        """Move the circle's clock forward (negative values are refused) and persist it."""
        if days < 0:
            raise ValueError("the virtual clock only moves forward")
        new_now = self.now() + timedelta(days=days)
        with session_scope() as db:
            row = db.get(Circle, self.circle.id)
            if row is None:
                raise LookupError(f"circle {self.circle.id} not found")
            row.virtual_now = new_now
            db.add(row)
        self.circle.virtual_now = new_now
        return new_now

    def iso_now(self) -> str:
        return iso(self.now()) or ""


def clock_for(circle_id: str) -> VirtualClock:
    with session_scope() as db:
        circle = db.get(Circle, circle_id)
    if circle is None:
        raise LookupError(f"circle {circle_id} not found")
    return VirtualClock(circle)


def virtual_now(circle_id: str) -> datetime:
    return clock_for(circle_id).now()


def advance_clock(circle_id: str, days: float) -> datetime:
    return clock_for(circle_id).advance(days)
