"""Leftovers from the live sandbox re-run (docs/live-run/2026-10-25-rerun.md), all with PayPal mocked.

- R2: a PENDING payout is re-read on a bounded schedule and every status change is stored and published;
- R3: a stale Balances reading is shown with its refresh time instead of "n/a";
- R4: a round's charges and toolkit confirmations overlap through the one FIFO gateway, in charter order;
- fees: the payout fee shown (result, events, board) is the one the ledger booked.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakes import FakePayPal
from sqlmodel import select

from app.db import session_scope
from app.engine import rounds, state
from app.models import LedgerRow, PayoutAttempt
from app.paypal.client import PayPalClient
from app.paypal.gateway import Gateway

MEMBERS = ["asha", "ravi", "meena", "tomas", "lin", "dev"]
NET = Decimal("192.53")


async def _collect_clean(circle_id: str, collector) -> None:
    state.set_flag(circle_id, "ravi_declines", False)
    await rounds.collect_round(circle_id, 3, publish=collector)


def _attempt(circle_id: str, round_n: int) -> PayoutAttempt:
    with session_scope() as db:
        return db.exec(
            select(PayoutAttempt).where(
                PayoutAttempt.circle_id == circle_id, PayoutAttempt.round_n == round_n
            )
        ).one()


def _fee_rows(circle_id: str, round_n: int) -> list[LedgerRow]:
    return [r for r in state.ledger_rows(circle_id) if r.kind == "payout_fee" and r.round_n == round_n]


# --- R4: concurrent charge + confirm through the FIFO gateway -------------------------------------------------


class SlowPayPal(FakePayPal):
    """FakePayPal whose every PayPal call is a blocking job on a REAL Gateway (like the toolkit's `requests`)."""

    def __init__(self, gw: Gateway, delay: float, **kw) -> None:
        super().__init__(**kw)
        self.gw = gw
        self.delay = delay
        self.starts: list[str] = []
        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0
        real_run = self.toolkit.run

        async def slow_run(method: str, params: dict, *, request_id: str | None = None) -> dict:
            await self.gw.run_sync(self._job(f"confirm:{params.get('order_id')}"), path=method)
            return await real_run(method, params, request_id=request_id)

        self.toolkit.run = slow_run  # type: ignore[method-assign]

    def _job(self, label: str):
        def run() -> None:
            with self._lock:
                self.starts.append(label)
                self.live += 1
                self.peak = max(self.peak, self.live)
            time.sleep(self.delay)
            with self._lock:
                self.live -= 1

        return run

    async def charge_vaulted(self, gw, **kw) -> dict:
        member = kw["reference_id"].split(":")[-1]
        await self.gw.run_sync(self._job(f"charge:{member}"), path="charge")
        return await super().charge_vaulted(gw, **kw)

    def install(self, monkeypatch) -> SlowPayPal:
        super().install(monkeypatch)
        monkeypatch.setattr(rounds, "_gateway", lambda: self.gw)
        return self


async def test_round_charges_and_confirms_overlap_in_charter_order(
    vaulted_circle, collector, monkeypatch, settings
):
    delay = 0.2
    gw = Gateway(PayPalClient(settings), rate_per_min=600, min_gap_s=0.02, max_inflight=4)
    paypal = SlowPayPal(gw, delay).install(monkeypatch)
    state.set_flag(vaulted_circle, "ravi_declines", True)
    try:
        started = time.perf_counter()
        results = await asyncio.wait_for(
            rounds.collect_round(vaulted_circle, 3, publish=collector), timeout=20
        )
        elapsed = time.perf_counter() - started
    finally:
        await gw.stop()

    # 6 charges + 5 confirmations = 11 calls; one after another that is 11 x 0.2 s = 2.2 s
    assert len(paypal.starts) == 11
    assert elapsed < 1.5, elapsed
    assert 2 <= paypal.peak <= 4  # they overlapped, never beyond the gateway's in-flight cap
    # the charges START in charter order (FIFO gateway), and each confirmation starts after its own charge
    assert [s for s in paypal.starts if s.startswith("charge:")] == [f"charge:{m}" for m in MEMBERS]
    for c in paypal.charges:
        member = c["reference_id"].split(":")[-1]
        if member == "ravi":
            continue
        order = next(r["order_id"] for r in results if r["member"] == member)
        assert paypal.starts.index(f"charge:{member}") < paypal.starts.index(f"confirm:{order}")
    # results in charter order; idempotency keys unchanged (one a1 key per member)
    assert [r["member"] for r in results] == MEMBERS
    keys = [c["request_id"] for c in paypal.charges]
    assert len(set(keys)) == 6 and all(k.endswith(":a1") for k in keys)
    by = {r["member"]: r for r in results}
    assert by["ravi"]["status"] == "DECLINED"
    assert all(by[m]["status"] == "COMPLETED" and by[m]["confirmed"] is True for m in MEMBERS if m != "ravi")
    # per member: the capture chip comes before the confirmation chip; the round events come last
    for m in MEMBERS:
        if m != "ravi":
            assert [c["label"] for c in collector.chips(m)] == ["Captured", "Confirmed via toolkit"]
    assert collector.events[-1]["type"] == "board"


async def test_collect_round_twice_never_charges_a_settled_member_again(vaulted_circle, paypal, collector):
    await _collect_clean(vaulted_circle, collector)
    again = await rounds.collect_round(vaulted_circle, 3, publish=collector)
    assert len(paypal.charges) == 6
    assert all(r.get("note") == "already settled" for r in again)
    assert [m for m, _ in paypal.toolkit.calls] == ["get_order_details"] * 6  # cached, not re-confirmed


# --- R2: bounded payout status polling ------------------------------------------------------------------------


@pytest.fixture
def watch_fast(monkeypatch):
    monkeypatch.setattr(rounds, "PAYOUT_WATCH_ENABLED", True)
    monkeypatch.setattr(rounds, "PAYOUT_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(rounds, "PAYOUT_POLL_MAX_S", 2.0)


async def test_pending_payout_is_polled_until_success_and_published(
    vaulted_circle, paypal_factory, collector, watch_fast
):
    paypal = paypal_factory(payout_status="PENDING", payout_fee=None)
    await _collect_clean(vaulted_circle, collector)
    collector.events.clear()
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert res["ok"] is True and res["status"] == "PENDING" and res["watching"] is True
    # PayPal named no fee yet: the ledger booked the 0.25 reserve, and that is what is shown
    assert res["fee"] == "0.25" and res["fee_source"] == "reserve"
    assert collector.of_type("payout")[0]["fee"] == "0.25"
    assert _attempt(vaulted_circle, 3).fee == "0.25"
    task = rounds._WATCHERS[res["item_id"]]

    await asyncio.sleep(0.05)  # a few polls while still PENDING: nothing changes, nothing is published
    assert len(paypal.item_polls) >= 2
    assert len(collector.of_type("payout")) == 1
    paypal.payout_status, paypal.payout_fee = "SUCCESS", "0.30"  # PayPal finishes the item (fee made up)
    assert await asyncio.wait_for(task, timeout=2) == "SUCCESS"

    rnd = state.get_round(vaulted_circle, 3)
    assert rnd.status == "PAID" and rnd.payout_status == "SUCCESS" and rnd.paid_at is not None
    attempt = _attempt(vaulted_circle, 3)
    assert attempt.status == "SUCCESS" and attempt.fee == "0.30"
    # the ledger's fee row now carries PayPal's fee, and the board shows that same figure
    assert [r.amount for r in _fee_rows(vaulted_circle, 3)] == ["0.30"]
    board = state.board_model(vaulted_circle)
    r3 = board["rounds"][2]
    assert r3["status"] == "PAID" and r3["payout"]["status"] == "SUCCESS" and r3["payout"]["fee"] == "0.30"
    assert r3["payout_attempts"][0]["fee"] == "0.30"
    pot = Decimal(res["amount"])
    assert Decimal(board["circle"]["circle_available"]) == NET * 6 - pot - Decimal("0.30")
    assert board["counters"]["rounds_paid_on_time"] == 3
    events = collector.of_type("payout")
    assert (
        events[-1]["status"] == "SUCCESS"
        and events[-1]["fee"] == "0.30"
        and events[-1]["source"] == "status poll"
    )
    assert collector.of_type("ticker")[-1]["paid_at"] is not None
    assert any("payout SUCCESS (status poll)" in t["text"] for t in collector.of_type("toast"))
    polls = len(paypal.item_polls)
    await asyncio.sleep(0.05)
    assert len(paypal.item_polls) == polls  # stopped at the final status
    assert res["item_id"] not in rounds._WATCHERS


async def test_payout_poll_is_bounded_then_hands_over_to_webhooks(vaulted_circle, paypal_factory, collector):
    paypal = paypal_factory(payout_status="PENDING")
    await _collect_clean(vaulted_circle, collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)  # watchers are off in tests by default
    assert res["watching"] is False
    collector.events.clear()
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    task = rounds.watch_payout(
        vaulted_circle,
        3,
        res["item_id"],
        "PENDING",
        publish=collector,
        sleep=fake_sleep,
        clock=lambda: now[0],
    )
    assert task is None  # switched off
    rounds.PAYOUT_WATCH_ENABLED = True
    try:
        task = rounds.watch_payout(
            vaulted_circle,
            3,
            res["item_id"],
            "PENDING",
            publish=collector,
            interval_s=5.0,
            max_s=120.0,
            sleep=fake_sleep,
            clock=lambda: now[0],
        )
        assert await asyncio.wait_for(task, timeout=2) == "PENDING"
    finally:
        rounds.PAYOUT_WATCH_ENABLED = False
    # every 5 s for 120 s: 24 reads at most, then a trace that webhooks take over; nothing changed meanwhile
    assert sleeps == [5.0] * 24 and len(paypal.item_polls) == 1 + 24  # +1: pay_round's own read
    assert state.get_round(vaulted_circle, 3).payout_status == "PENDING"
    assert collector.of_type("payout") == []
    trace = collector.of_type("trace")[-1]["text"]
    assert "still PENDING after 120 s" in trace and "webhook" in trace


async def test_watcher_stops_when_a_webhook_or_reroute_got_there_first(
    vaulted_circle, paypal_factory, collector, monkeypatch
):
    paypal = paypal_factory(payout_status="PENDING")
    await _collect_clean(vaulted_circle, collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    monkeypatch.setattr(rounds, "PAYOUT_WATCH_ENABLED", True)

    async def no_sleep(_s: float) -> None:
        return None

    # a webhook already stored SUCCESS: the watcher reads the round and stops without a PayPal call
    view = rounds.record_payout_status(vaulted_circle, 3, res["item_id"], "SUCCESS", Decimal("0.25"))
    assert view["became_paid"] is True and view["changed"] is True
    polls = len(paypal.item_polls)
    task = rounds.watch_payout(
        vaulted_circle, 3, res["item_id"], "PENDING", publish=collector, sleep=no_sleep
    )
    assert await asyncio.wait_for(task, timeout=2) == "SUCCESS"
    assert len(paypal.item_polls) == polls
    # an item that is no longer the round's payout (re-routed) is left alone
    assert rounds.record_payout_status(vaulted_circle, 3, "SOME-OLD-ITEM", "SUCCESS") is None
    task = rounds.watch_payout(
        vaulted_circle, 3, "SOME-OLD-ITEM", "PENDING", publish=collector, sleep=no_sleep
    )
    assert await asyncio.wait_for(task, timeout=2) is None
    # final, unknown and replay items are never watched
    assert rounds.watch_payout(vaulted_circle, 3, res["item_id"], "SUCCESS", publish=collector) is None
    assert rounds.watch_payout(vaulted_circle, 3, None, "PENDING", publish=collector) is None
    assert rounds.watch_payout(vaulted_circle, 3, "REPLAY-ITEM-3", "PENDING", publish=collector) is None


async def test_on_time_counter_counts_a_late_success_once(vaulted_circle, paypal_factory, collector):
    paypal_factory(payout_status="PENDING")
    await _collect_clean(vaulted_circle, collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    before = state.board_model(vaulted_circle)["counters"]["rounds_paid_on_time"]
    rounds.record_payout_status(vaulted_circle, 3, res["item_id"], "SUCCESS")
    rounds.record_payout_status(vaulted_circle, 3, res["item_id"], "SUCCESS")  # a duplicate report
    assert state.board_model(vaulted_circle)["counters"]["rounds_paid_on_time"] == before + 1


async def test_payout_status_read_updates_round_attempt_and_fee(vaulted_circle, paypal_factory, collector):
    paypal = paypal_factory(payout_status="PENDING")
    await _collect_clean(vaulted_circle, collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    paypal.payout_status = "SUCCESS"
    out = await rounds.payout_status(vaulted_circle, 3)
    assert out["status"] == "SUCCESS" and out["round_status"] == "PAID" and out["fee"] == "0.25"
    assert _attempt(vaulted_circle, 3).status == "SUCCESS"
    assert res["item_id"] == out["item_id"]


# --- R3: a stale Balances reading is shown with its refresh time -----------------------------------------------


async def test_stale_balance_is_shown_with_its_refresh_time(vaulted_circle, paypal, monkeypatch):
    refreshed = (datetime.now(UTC) - timedelta(hours=1, minutes=33)).replace(microsecond=0)

    async def stale_balances(gw, *, currency="USD", as_of_time=None):
        return {
            "balances": [
                {
                    "currency": currency,
                    "primary": True,
                    "available_balance": {"currency_code": currency, "value": "7885.93"},
                }
            ],
            "last_refresh_time": refreshed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    monkeypatch.setattr(rounds._balances(), "get_balances", stale_balances)
    res = await rounds.available_balance(vaulted_circle)
    # the contract holds: a stale figure never becomes the binding paypal_available
    assert res["paypal_available"] is None and res["source"] == "ledger"
    when = refreshed.strftime("%Y-%m-%d %H:%M UTC")
    assert res["paypal_balance_label"] == f"7885.93 USD (stale: last refreshed {when}, 93 min old)"
    assert res["note"] == f"PayPal balance {res['paypal_balance_label']}; context only, the ledger is binding"
    assert "n/a" not in res["note"]
