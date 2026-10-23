"""D1 sandbox findings in the PayPal wrappers: Balances freshness and the bounded wait before a cancel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.paypal import balances, payouts
from app.paypal.client import PayPalError


def _bal(value: str, refreshed: str | None) -> dict:
    data: dict = {
        "balances": [{"currency": "USD", "available_balance": {"currency_code": "USD", "value": value}}]
    }
    if refreshed is not None:
        data["last_refresh_time"] = refreshed
    return data


def test_balance_context_marks_a_lagging_reading_stale():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    old = (now - timedelta(hours=2, minutes=30)).isoformat().replace("+00:00", "Z")
    ctx = balances.balance_context(_bal("5000.00", old), now=now)
    assert ctx == {
        "available": "5000.00",
        "last_refresh_time": old,
        "source": "paypal_balances",
        "stale": True,
        "age_s": 9000,
    }
    fresh = balances.balance_context(_bal("5000.00", (now - timedelta(minutes=5)).isoformat()), now=now)
    assert fresh["stale"] is False and fresh["age_s"] == 300
    unknown = balances.balance_context(_bal("1.00", None), now=now)
    assert unknown["stale"] is None and unknown["age_s"] is None and unknown["available"] == "1.00"
    garbage = balances.balance_context(_bal("1.00", "not a time"), now=now)
    assert garbage["stale"] is None
    empty = balances.balance_context({"balances": []}, now=now)
    assert empty["available"] is None


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s

    def __call__(self) -> float:
        return self.t


async def test_wait_for_batch_final_backs_off_until_success():
    script = iter(["PENDING", "PROCESSING", "PROCESSING", "SUCCESS"])
    calls: list[str] = []

    async def fetch(gw, *, batch_id):
        calls.append(batch_id)
        return {"batch_header": {"payout_batch_id": batch_id, "batch_status": next(script)}}

    clock = _Clock()
    batch, final = await payouts.wait_for_batch_final(
        None, batch_id="B1", fetch=fetch, sleep=clock.sleep, clock=clock
    )
    assert final is True and payouts.batch_status(batch) == "SUCCESS"
    assert clock.sleeps == [2.0, 4.0, 8.0] and len(calls) == 4


async def test_wait_for_batch_final_is_bounded_and_tolerates_errors():
    async def fetch(gw, *, batch_id):
        raise PayPalError(500, {"name": "INTERNAL_SERVICE_ERROR", "message": "boom"})

    clock = _Clock()
    batch, final = await payouts.wait_for_batch_final(
        None, batch_id="B1", fetch=fetch, sleep=clock.sleep, clock=clock, max_wait_s=60
    )
    assert batch is None and final is False
    assert clock.t == 60.0 and clock.sleeps[:4] == [2.0, 4.0, 8.0, 15.0] and max(clock.sleeps) == 15.0


def test_cancel_label_is_honest():
    assert payouts.CANCEL_UNAVAILABLE_LABEL == "Cancel not available in sandbox"
