from datetime import timedelta

import pytest

from app.engine import state
from app.engine.clock import VirtualClock, advance_clock, clock_for, virtual_now


def test_advance_persists_on_the_circle(circle_id):
    before = virtual_now(circle_id)
    after = advance_clock(circle_id, 2.5)
    assert after - before == timedelta(days=2.5)
    assert state.load_circle(circle_id).virtual_now == after
    assert clock_for(circle_id).today() == after.date()


def test_clock_never_moves_backwards(circle_id):
    with pytest.raises(ValueError):
        VirtualClock(state.load_circle(circle_id)).advance(-1)


def test_unknown_circle(circle_id):
    with pytest.raises(LookupError):
        clock_for("zzzzzz")
