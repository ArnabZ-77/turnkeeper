"""D1 learnings in the round engine: UNCLAIMED re-route, stale Balances as context, simulated rounds.

All PayPal access is faked (tests/engine/fakes.py); nothing reaches the network.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fakes import FakePayPal
from reroute_fakes import FakeClock, RerouteFake
from sqlmodel import select

from app.db import session_scope
from app.engine import rounds, state
from app.models import LedgerRow, PayoutAttempt

NET = Decimal("192.53")
POT = NET * 6 - Decimal("0.25")


@pytest.fixture(autouse=True)
def fresh_balance_cache():
    state.clear_paypal_balance()
    yield
    state.clear_paypal_balance()


@pytest.fixture
def reroute_factory(monkeypatch):
    def _make(**kw: Any) -> RerouteFake:
        return RerouteFake(**kw).install(monkeypatch)

    return _make


async def _unclaimed_round3(cid: str, collector) -> dict:
    state.set_flag(cid, "ravi_declines", False)
    state.set_flag(cid, "next_payout_unclaimed", True)
    await rounds.collect_round(cid, 3, publish=collector)
    res = await rounds.pay_round(cid, 3, publish=collector)
    assert res["ok"] is True and res["status"] == "UNCLAIMED" and res["rev"] == 1
    return res


def _ledger(cid: str, kind: str, round_n: int | None = 3) -> list[LedgerRow]:
    """Rows of `kind` for round 3 (the fixture's settled rounds 1-2 have their own), or all with round_n=None."""
    with session_scope() as db:
        rows = db.exec(select(LedgerRow).where(LedgerRow.circle_id == cid, LedgerRow.kind == kind)).all()
    return [r for r in rows if round_n is None or r.round_n == round_n]


async def test_reroute_when_cancel_fails_shows_pending_return_and_never_double_counts(
    vaulted_circle, reroute_factory, collector
):
    paypal = reroute_factory(cancel="fail500")
    first = await _unclaimed_round3(vaulted_circle, collector)
    sent = paypal.payouts[0]
    assert sent["items"][0]["receiver"].startswith("unclaimed-")
    circle_before, buffer_before = state.ledger_totals(vaulted_circle)
    clock = FakeClock()

    res = await rounds.reroute_unclaimed(vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock)

    assert res["ok"] is True, res
    assert res["rev"] == 2 and res["status"] == "SUCCESS" and res["funding"] == "fronted_pending_return"
    # waited for the batch (PROCESSING -> SUCCESS) with backoff, then exactly one cancel attempt
    assert clock.sleeps and clock.sleeps[0] == 2.0
    assert paypal.cancel_calls == [first["item_id"]]
    assert res["cancel"] == {
        "attempted": True,
        "ok": False,
        "status": "cancel_failed",
        "error": "500 INTERNAL_SERVICE_ERROR",
        "label": "Cancel not available in sandbox",
        "batch_status": "SUCCESS",
        "batch_final": True,
    }
    # the new batch is r2 to the member's real receiver, with a distinct item id
    circle = state.load_circle(vaulted_circle)
    second = paypal.payouts[1]
    assert second["sender_batch_id"] == f"tk:{circle.run_id}:{vaulted_circle}:3:r2"
    assert second["items"][0]["receiver"] == "dev@example.com"
    assert second["items"][0]["sender_item_id"].endswith("3devr2")
    assert Decimal(str(second["items"][0]["amount"])) == POT
    # ledger: no refund (the cancel failed) and no second payout row; only the re-route fee, covered by the buffer
    circle_after, buffer_after = state.ledger_totals(vaulted_circle)
    assert circle_after == circle_before
    assert buffer_after == buffer_before - Decimal("0.25")
    assert _ledger(vaulted_circle, "refund") == []
    assert len(_ledger(vaulted_circle, "payout")) == 1
    fee_cover = [
        r for r in _ledger(vaulted_circle, "buffer_advance", None) if (r.ref or "").startswith("reroute-fee:")
    ]
    assert len(fee_cover) == 1 and fee_cover[0].member_handle is None and fee_cover[0].round_n is None
    assert state.open_advances(vaulted_circle) == {}

    board = state.board_model(vaulted_circle)
    r3 = board["rounds"][2]
    assert r3["status"] == "PAID" and r3["payout_rev"] == 2
    assert r3["payout"]["item_id"] == res["item_id"] and r3["payout"]["status"] == "SUCCESS"
    assert r3["pending_return"] == {
        "amount": str(POT),
        "items": [{"rev": 1, "item_id": first["item_id"], "amount": str(POT)}],
        "label": "pending return",
    }
    revs = {a["rev"]: a for a in r3["payout_attempts"]}
    assert revs[1]["cancel_status"] == "cancel_failed" and revs[1]["return_status"] == "pending_return"
    assert (
        "Cancel not available in sandbox" in revs[1]["label"] and revs[1]["receiver_kind"] == "unclaimed_test"
    )
    assert revs[2]["funding"] == "fronted_pending_return" and revs[2]["receiver_kind"] == "payout_receiver"
    assert not any("@" in str(a) for a in r3["payout_attempts"])  # receivers never reach the board

    ev = [e for e in collector.of_type("payout") if e.get("rev") == 2][0]
    assert ev["pending_return"]["label"] == "pending return" and ev["cancel_status"] == "cancel_failed"

    # the first item finally returns: pending return clears, the circle ledger does not move
    assert rounds.mark_payout_item_returned(first["item_id"])["rev"] == 1
    assert rounds.mark_payout_item_returned(first["item_id"]) is None  # idempotent
    assert state.board_model(vaulted_circle)["rounds"][2]["pending_return"] is None
    assert state.ledger_totals(vaulted_circle) == (circle_after, buffer_after)

    # nothing left to re-route
    again = await rounds.reroute_unclaimed(
        vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock
    )
    assert again["ok"] is False and "only an UNCLAIMED payout" in again["denied"]


async def test_reroute_after_a_successful_cancel_refunds_then_pays(
    vaulted_circle, reroute_factory, collector
):
    paypal = reroute_factory(cancel="ok", batch_script=("SUCCESS",))
    first = await _unclaimed_round3(vaulted_circle, collector)
    circle_before, buffer_before = state.ledger_totals(vaulted_circle)
    clock = FakeClock()
    res = await rounds.reroute_unclaimed(vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock)
    assert res["ok"] is True and res["funding"] == "refund" and res["pending_return"] is None
    assert res["cancel"]["ok"] is True and res["cancel"]["status"] == "canceled"
    assert clock.sleeps == []  # the batch was already final
    refunds = _ledger(vaulted_circle, "refund")
    assert (
        len(refunds) == 1
        and refunds[0].ref == f"cancel:{first['item_id']}"
        and Decimal(refunds[0].amount) == POT
    )
    assert len(_ledger(vaulted_circle, "payout")) == 2  # refund + second payout net to one pot
    assert state.ledger_totals(vaulted_circle) == (circle_before, buffer_before - Decimal("0.25"))
    assert len(paypal.payouts) == 2
    r3 = state.board_model(vaulted_circle)["rounds"][2]
    assert r3["pending_return"] is None and r3["payout_rev"] == 2
    assert {a["rev"]: a["return_status"] for a in r3["payout_attempts"]} == {1: "returned", 2: None}


async def test_reroute_waits_at_most_the_limit_then_tries_once(vaulted_circle, reroute_factory, collector):
    paypal = reroute_factory(cancel="fail400", batch_script=("PROCESSING",))
    await _unclaimed_round3(vaulted_circle, collector)
    clock = FakeClock()
    res = await rounds.reroute_unclaimed(
        vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock, max_wait_s=60
    )
    assert clock.now == pytest.approx(60.0) and len(paypal.cancel_calls) == 1
    assert res["ok"] is True and res["cancel"]["batch_final"] is False
    assert res["cancel"]["error"] == "400 BATCH_NOT_COMPLETED"


async def test_reroute_gates(vaulted_circle, reroute_factory, collector):
    reroute_factory(cancel="fail500", first_status="SUCCESS")
    state.set_flag(vaulted_circle, "ravi_declines", False)
    none_yet = await rounds.reroute_unclaimed(vaulted_circle, 3, publish=collector)
    assert none_yet["ok"] is False and "no payout to re-route" in none_yet["denied"]
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    await rounds.pay_round(vaulted_circle, 3, publish=collector)
    claimed = await rounds.reroute_unclaimed(vaulted_circle, 3, publish=collector)
    assert claimed["ok"] is False and "is SUCCESS" in claimed["denied"]


async def test_reroute_needs_another_receiver(circle_id, reroute_factory, collector):
    from fakes import vault_members

    vault_members(circle_id)
    paypal = reroute_factory(cancel="fail500")
    state.set_flag(circle_id, "ravi_declines", False)
    await rounds.collect_round(circle_id, 3, publish=collector)
    res = await rounds.pay_round(circle_id, 3, publish=collector)  # a real receiver that went UNCLAIMED
    assert res["status"] == "UNCLAIMED"
    clock = FakeClock()
    out = await rounds.reroute_unclaimed(circle_id, 3, publish=collector, sleep=clock.sleep, clock=clock)
    assert out["ok"] is False and "no other payout receiver" in out["denied"]
    assert paypal.cancel_calls == [] and len(paypal.payouts) == 1


async def test_stale_balance_never_blocks_a_payout(vaulted_circle, monkeypatch, collector):
    paypal = FakePayPal(balance="10.00").install(monkeypatch)

    async def stale_balances(gw, *, currency="USD", as_of_time=None):
        return {
            "balances": [
                {"currency": currency, "available_balance": {"currency_code": currency, "value": "10.00"}}
            ],
            "last_refresh_time": "2020-01-01T00:00:00Z",
        }

    monkeypatch.setattr(paypal, "get_balances", stale_balances)
    paypal.install(monkeypatch)
    bal = await rounds.available_balance(vaulted_circle)
    assert bal["paypal_available"] is None and bal["source"] == "ledger"
    assert bal["paypal_balance"]["stale"] is True and bal["paypal_balance"]["available"] == "10.00"
    assert bal["paypal_balance"]["source"] == "paypal_balances" and "stale" in bal["note"]

    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert res["ok"] is True and res["status"] == "SUCCESS" and res["source"] == "ledger"
    board = state.board_model(vaulted_circle)
    assert board["circle"]["paypal_balance"]["value"] == "10.00"
    assert board["circle"]["paypal_balance"]["last_refreshed"] == "2020-01-01T00:00:00Z"
    assert board["circle"]["paypal_balance"]["stale"] is True


async def test_fresh_low_balance_still_blocks(vaulted_circle, monkeypatch, collector):
    from datetime import UTC, datetime

    paypal = FakePayPal().install(monkeypatch)

    async def fresh_low(gw, *, currency="USD", as_of_time=None):
        return {
            "balances": [
                {"currency": currency, "available_balance": {"currency_code": currency, "value": "10.00"}}
            ],
            "last_refresh_time": datetime.now(UTC).isoformat(),
        }

    monkeypatch.setattr(paypal, "get_balances", fresh_low)
    paypal.install(monkeypatch)
    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert res["ok"] is False and "PayPal available balance" in res["denied"]


async def test_pay_round_records_a_payout_attempt(vaulted_circle, paypal, collector):
    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    with session_scope() as db:
        rows = list(db.exec(select(PayoutAttempt).where(PayoutAttempt.circle_id == vaulted_circle)).all())
    assert len(rows) == 1
    a = rows[0]
    assert (a.rev, a.item_id, a.status, a.funding, a.receiver_kind) == (
        1,
        res["item_id"],
        "SUCCESS",
        "ledger",
        "payout_receiver",
    )
    assert a.amount == res["amount"] and a.fee == "0.25"


async def test_simulated_rounds_never_pay(circle_id, paypal, collector):
    from app.engine import recovery

    await recovery.simulate_round_decline(circle_id, 3, "ravi", publish=collector)
    out = await rounds.simulate_clean_round(circle_id, 4, publish=collector)
    assert out["ok"] is True and out["settled"] is True and len(out["captured"]) == 6
    board = state.board_model(circle_id)
    r4 = [c for c in board["contributions"] if c["round_n"] == 4]
    assert all(c["status"] == "COMPLETED" and c["order_id"].startswith("REPLAY-ORD-4-") for c in r4)
    assert all(c["label"] == rounds.SIMULATED_CAPTURE_LABEL for c in r4)
    assert board["rounds"][3]["status"] == "COLLECTED"
    res = await rounds.pay_round(circle_id, 4, publish=collector)
    assert res["ok"] is False and res["simulated"] is True and "simulated" in res["denied"]
    assert paypal.payouts == [] and paypal.charges == []
    assert any("Payout skipped" in e.get("text", "") for e in collector.of_type("trace"))


# --- fixer regressions ---------------------------------------------------------------------------------


async def test_cancel_with_an_empty_answer_is_never_counted_as_returned(
    vaulted_circle, reroute_factory, collector
):
    """A 200/204 cancel with no transaction_status is unconfirmed: no refund, the first item stays pending."""
    paypal = reroute_factory(cancel="empty", batch_script=("SUCCESS",))
    first = await _unclaimed_round3(vaulted_circle, collector)
    clock = FakeClock()
    res = await rounds.reroute_unclaimed(vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock)
    assert res["ok"] is True and res["funding"] == "fronted_pending_return"
    assert res["cancel"]["ok"] is False and res["cancel"]["status"] == "cancel_failed"
    assert res["cancel"]["error"] == "cancel unconfirmed (UNCLAIMED)"
    assert _ledger(vaulted_circle, "refund") == []
    assert paypal.cancel_calls == [first["item_id"]]
    r3 = state.board_model(vaulted_circle)["rounds"][2]
    assert r3["pending_return"]["items"][0]["item_id"] == first["item_id"]
    assert not any("funds returned" in str(e.get("text", "")) for e in collector.events)


async def test_reroute_resumes_after_the_new_batch_failed(vaulted_circle, reroute_factory, collector):
    """Cancel succeeded (refund booked) but the re-route send failed: a second call sends it, never re-cancels
    and never re-books the refund."""
    paypal = reroute_factory(cancel="ok", batch_script=("SUCCESS",))
    first = await _unclaimed_round3(vaulted_circle, collector)
    circle_before, buffer_before = state.ledger_totals(vaulted_circle)
    clock = FakeClock()
    paypal.payout_error = True
    failed = await rounds.reroute_unclaimed(
        vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock
    )
    assert failed["ok"] is False and failed["cancel"]["ok"] is True
    assert state.get_round(vaulted_circle, 3).payout_status == "RETURNED"
    assert len(_ledger(vaulted_circle, "refund")) == 1

    paypal.payout_error = False
    res = await rounds.reroute_unclaimed(vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock)
    assert res["ok"] is True, res
    assert res["rev"] == 2 and res["funding"] == "refund" and res["cancel"].get("resumed") is True
    assert paypal.cancel_calls == [first["item_id"]]  # exactly one cancel overall
    assert len(paypal.payouts) == 2 and paypal.payouts[1]["items"][0]["receiver"] == "dev@example.com"
    assert len(_ledger(vaulted_circle, "refund")) == 1 and len(_ledger(vaulted_circle, "payout")) == 2
    assert state.ledger_totals(vaulted_circle) == (circle_before, buffer_before - Decimal("0.25"))
    r3 = state.board_model(vaulted_circle)["rounds"][2]
    assert r3["status"] == "PAID" and r3["payout_rev"] == 2
    assert {a["rev"]: a["return_status"] for a in r3["payout_attempts"]} == {1: "returned", 2: None}
    again = await rounds.reroute_unclaimed(
        vaulted_circle, 3, publish=collector, sleep=clock.sleep, clock=clock
    )
    assert again["ok"] is False
