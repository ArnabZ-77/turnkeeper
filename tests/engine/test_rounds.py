"""collect_round / charge_member / confirm_capture / available_balance / pay_round against PayPal fakes."""

from decimal import Decimal

from sqlmodel import select

from app.db import session_scope
from app.engine import rounds, state
from app.engine.clock import advance_clock
from app.engine.rounds import MOCK_DECLINE_LABEL
from app.models import LedgerRow

MEMBERS = ["asha", "ravi", "meena", "tomas", "lin", "dev"]
NET = Decimal("192.53")  # 200.00 - 7.47


def _by_member(results: list[dict]) -> dict[str, dict]:
    return {r["member"]: r for r in results}


async def test_collect_round_with_mocked_ravi_decline(vaulted_circle, paypal, collector):
    results = await rounds.collect_round(vaulted_circle, 3, publish=collector)
    assert [r["member"] for r in results] == MEMBERS
    assert all(list(r)[0] == "ok" for r in results)
    by = _by_member(results)

    ravi = by["ravi"]
    assert ravi["ok"] is False and ravi["status"] == "DECLINED"
    assert ravi["label"] == MOCK_DECLINE_LABEL
    assert ravi["decline_code"] == "INSTRUMENT_DECLINED"
    assert ravi["error"] == "UNPROCESSABLE_ENTITY" and ravi["issues"] == ["INSTRUMENT_DECLINED"]
    assert ravi["attempt"] == 1 and ravi["order_id"] is None

    for h in MEMBERS:
        if h == "ravi":
            continue
        r = by[h]
        assert r["ok"] is True and r["status"] == "COMPLETED"
        assert r["order_id"].startswith("FAKEORD") and r["capture_id"].startswith("FAKECAP")
        assert (r["gross"], r["fee"], r["net"]) == ("200.00", "7.47", "192.53")

    # the mocked member got the PayPal-Mock-Response path; everyone else did not
    mocked = [c["reference_id"] for c in paypal.charges if c["mock_decline"]]
    assert mocked == [f"{vaulted_circle}:3:ravi"]
    assert all(c["amount"] == Decimal("200.00") and c["currency"] == "USD" for c in paypal.charges)
    circle = state.load_circle(vaulted_circle)
    assert all(
        c["request_id"] == f"tk:{circle.run_id}:{vaulted_circle}:3:{c['reference_id'].split(':')[-1]}:a1"
        for c in paypal.charges
    )
    assert all(c["vault_id"].startswith("FAKEVAULT") for c in paypal.charges)

    # persisted rows + chips
    board = state.board_model(vaulted_circle)
    row = {c["member"]: c for c in board["contributions"] if c["round_n"] == 3}
    assert row["ravi"]["status"] == "DECLINED" and row["ravi"]["decline_source"] == "api"
    assert row["ravi"]["label"] == MOCK_DECLINE_LABEL and row["ravi"]["decline_code"] == "INSTRUMENT_DECLINED"
    assert row["asha"]["status"] == "COMPLETED" and row["asha"]["webhook_verified"] is False
    chips = collector.chips()
    assert [c["member"] for c in chips] == MEMBERS
    ravi_chip = collector.chips("ravi")[0]
    assert ravi_chip["status"] == "DECLINED" and ravi_chip["label"] == MOCK_DECLINE_LABEL
    assert {"round_n", "member", "status", "order_id", "capture_id", "label", "decline_code"} <= set(
        ravi_chip
    )
    assert collector.of_type("queue") and collector.of_type("ticker") and collector.of_type("board")

    # round stays COLLECTING, ledger has five nets, circle_available == 5 * net
    assert board["rounds"][2]["status"] == "COLLECTING"
    assert Decimal(board["circle"]["circle_available"]) == NET * 5
    with session_scope() as db:
        nets = db.exec(
            select(LedgerRow).where(
                LedgerRow.circle_id == vaulted_circle,
                LedgerRow.round_n == 3,
                LedgerRow.kind == "contribution_net",
            )
        ).all()
    assert sorted(r.member_handle for r in nets) == sorted(h for h in MEMBERS if h != "ravi")
    assert all(r.ref.startswith("FAKECAP") for r in nets)


async def test_collect_round_clean_marks_round_collected(vaulted_circle, paypal, collector):
    state.set_flag(vaulted_circle, "ravi_declines", False)
    results = await rounds.collect_round(vaulted_circle, 3, publish=collector)
    assert all(r["status"] == "COMPLETED" for r in results)
    assert not any(c["mock_decline"] for c in paypal.charges)
    board = state.board_model(vaulted_circle)
    assert board["rounds"][2]["status"] == "COLLECTED"
    assert board["ticker"]["last_capture_at"] is not None and board["ticker"]["paid_at"] is None
    toasts = [t["text"] for t in collector.of_type("toast")]
    assert any("all contributions collected" in t for t in toasts)


async def test_collect_round_is_idempotent_for_settled_members(vaulted_circle, paypal, collector):
    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    n = len(paypal.charges)
    again = await rounds.collect_round(vaulted_circle, 3, publish=collector)
    assert len(paypal.charges) == n  # no second charge for settled members
    assert all(r["ok"] and r.get("note") == "already settled" for r in again)


async def test_collect_unknown_round_or_circle(vaulted_circle, paypal, collector):
    assert (await rounds.collect_round(vaulted_circle, 99, publish=collector))[0]["ok"] is False
    assert (await rounds.collect_round("zzzzzz", 3, publish=collector))[0]["ok"] is False


async def test_charge_member_without_vault_id(circle_id, paypal, collector):
    res = await rounds.charge_member(circle_id, 3, "asha", publish=collector)
    assert res["ok"] is False and res["error"] == "NOT_VAULTED" and res["status"] == "PENDING"
    assert paypal.charges == []
    assert collector.chips("asha")[0]["label"].startswith("No vault id")


async def test_charge_member_unknown_handle(vaulted_circle, paypal, collector):
    res = await rounds.charge_member(vaulted_circle, 3, "nobody", publish=collector)
    assert res["ok"] is False and res["error"] == "unknown member or round"


async def test_retry_after_decline_needs_24_virtual_hours(vaulted_circle, paypal, collector):
    first = await rounds.charge_member(vaulted_circle, 3, "ravi", publish=collector)
    assert first["status"] == "DECLINED" and first["attempt"] == 1
    too_soon = await rounds.charge_member(vaulted_circle, 3, "ravi", publish=collector)
    assert too_soon["ok"] is False and "too soon" in too_soon["denied"]
    assert len(paypal.charges) == 1
    advance_clock(vaulted_circle, 1)
    state.set_flag(vaulted_circle, "ravi_declines", False)
    retry = await rounds.charge_member(vaulted_circle, 3, "ravi", publish=collector)
    assert retry["ok"] is True and retry["status"] == "COMPLETED" and retry["attempt"] == 2
    assert paypal.charges[-1]["request_id"].endswith(":ravi:a2")


async def test_retry_limit_is_two_retries(vaulted_circle, paypal, collector):
    for _ in range(3):
        res = await rounds.charge_member(vaulted_circle, 3, "ravi", publish=collector)
        assert res["status"] == "DECLINED"
        advance_clock(vaulted_circle, 1)
    denied = await rounds.charge_member(vaulted_circle, 3, "ravi", publish=collector)
    assert denied["ok"] is False and denied.get("denied")
    assert len(paypal.charges) == 3


async def test_frozen_circle_denies_charges(vaulted_circle, paypal, collector, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TURNKEEPER_FREEZE", "1")
    get_settings.cache_clear()
    res = await rounds.charge_member(vaulted_circle, 3, "asha", publish=collector)
    assert res["ok"] is False and res.get("denied")
    assert paypal.charges == []


async def test_non_decline_paypal_error_keeps_pending(vaulted_circle, paypal_factory, collector, monkeypatch):
    from app.paypal.client import PayPalError

    paypal = paypal_factory()

    async def boom(gw, **kw):
        raise PayPalError(500, {"name": "INTERNAL_SERVER_ERROR", "message": "oops"})

    monkeypatch.setattr(paypal, "charge_vaulted", boom)
    paypal.install(monkeypatch)
    res = await rounds.charge_member(vaulted_circle, 3, "asha", publish=collector)
    assert res["ok"] is False and res["status"] == "PENDING" and res["error"] == "INTERNAL_SERVER_ERROR"
    assert res["label"] == "PayPal error: INTERNAL_SERVER_ERROR" and res["decline_code"] is None


async def test_confirm_capture_via_toolkit(vaulted_circle, paypal, collector):
    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    res = await rounds.confirm_capture(vaulted_circle, 3, "asha", publish=collector)
    assert res["ok"] is True and res["confirmed"] is True and res["capture_status"] == "COMPLETED"
    assert paypal.toolkit.prepared == 1
    assert paypal.toolkit.calls[-1][0] == "get_order_details"
    assert paypal.toolkit.calls[-1][1]["order_id"] == res["order_id"]
    assert collector.chips("asha")[-1]["label"] == "Confirmed via toolkit"
    # replayed rounds never hit PayPal
    replay = await rounds.confirm_capture(vaulted_circle, 1, "asha", publish=collector)
    assert replay["ok"] and replay["replay"] is True and len(paypal.toolkit.calls) == 1
    # nothing to confirm for a pending member
    none = await rounds.confirm_capture(vaulted_circle, 4, "asha", publish=collector)
    assert none["ok"] is False and none["error"] == "NO_ORDER"


async def test_available_balance_sources(vaulted_circle, paypal_factory):
    paypal_factory(balance="123.45")
    res = await rounds.available_balance(vaulted_circle)
    assert list(res)[0] == "ok" and res["ok"] is True
    assert res == {
        "ok": True,
        "paypal_available": "123.45",
        "circle_available": "0.00",
        "buffer_balance": "250.00",
        "source": "balances",
        "note": None,
    }
    paypal_factory(balances_error=True)
    res = await rounds.available_balance(vaulted_circle)
    assert (
        res["paypal_available"] is None and res["source"] == "ledger" and "ledger is binding" in res["note"]
    )
    paypal_factory(balance=None)
    res = await rounds.available_balance(vaulted_circle)
    assert res["paypal_available"] is None and res["source"] == "ledger"


async def test_pay_round_success(vaulted_circle, paypal, collector):
    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    collector.events.clear()
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert list(res)[0] == "ok" and res["ok"] is True
    pot = NET * 6 - Decimal("0.25")
    assert res["amount"] == str(pot) and res["recipient"] == "dev" and res["wallet"] == "PAYPAL"
    assert (
        res["status"] == "SUCCESS" and res["batch_id"] == "FAKEBATCH001" and res["item_id"] == "FAKEITEM001"
    )
    assert res["source"] == "balances" and res["fee"] == "0.25"

    circle = state.load_circle(vaulted_circle)
    sent = paypal.payouts[0]
    assert sent["sender_batch_id"] == f"tk:{circle.run_id}:{vaulted_circle}:3:r1"
    assert sent["items"][0]["receiver"] == "dev@example.com"
    assert sent["items"][0]["recipient_type"] == "EMAIL" and sent["items"][0]["wallet"] == "PAYPAL"
    assert sent["items"][0]["sender_item_id"] == f"{circle.run_id}{vaulted_circle}3dev"
    assert Decimal(str(sent["items"][0]["amount"])) == pot

    board = state.board_model(vaulted_circle)
    r3 = board["rounds"][2]
    assert r3["status"] == "PAID" and r3["paid_at"] is not None
    assert r3["payout"] == {
        "batch_id": "FAKEBATCH001",
        "item_id": "FAKEITEM001",
        "status": "SUCCESS",
        "amount": str(pot),
    }
    assert Decimal(board["circle"]["circle_available"]) == NET * 6 - pot - Decimal("0.25")
    assert board["counters"]["rounds_paid_on_time"] == 3
    assert board["ticker"]["paid_at"] is not None and board["ticker"]["seconds"] is not None

    payout_ev = collector.of_type("payout")[0]
    assert {"round_n", "status", "batch_id", "item_id", "amount", "recipient", "wallet"} <= set(payout_ev)
    assert payout_ev["status"] == "SUCCESS" and payout_ev["recipient"] == "dev"
    assert collector.of_type("ticker") and collector.of_type("board")

    again = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert again["ok"] is False and "already paid" in again["denied"]
    status = await rounds.payout_status(vaulted_circle, 3)
    assert status["ok"] and status["status"] == "SUCCESS" and status["item_id"] == "FAKEITEM001"


async def test_pay_round_holds_while_contributions_outstanding(vaulted_circle, paypal, collector):
    await rounds.collect_round(vaulted_circle, 3, publish=collector)  # ravi declines
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert (
        res["ok"] is False
        and res["denied"].startswith("contributions outstanding")
        and res["outstanding"] == ["ravi"]
    )
    assert paypal.payouts == []
    assert state.board_model(vaulted_circle)["rounds"][2]["status"] == "COLLECTING"


async def test_pay_round_needs_a_receiver_and_a_mandate(circle_id, paypal, collector):
    from fakes import vault_members

    vault_members(circle_id, receiver=False)
    state.set_flag(circle_id, "ravi_declines", False)
    await rounds.collect_round(circle_id, 3, publish=collector)
    res = await rounds.pay_round(circle_id, 3, publish=collector)
    assert res["ok"] is False and "no payout receiver" in res["denied"]

    vault_members(circle_id)
    from app.models import MandateRow

    with session_scope() as db:
        for row in db.exec(select(MandateRow).where(MandateRow.circle_id == circle_id)).all():
            db.delete(row)
    res = await rounds.pay_round(circle_id, 3, publish=collector)
    assert res["ok"] is False and res["denied"].startswith("mandate:")
    assert paypal.payouts == []


async def test_pay_round_respects_paypal_balance(vaulted_circle, paypal_factory, collector):
    paypal = paypal_factory(balance="10.00")
    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert res["ok"] is False and "PayPal available balance" in res["denied"]
    assert paypal.payouts == []


async def test_pay_round_unclaimed_flag_and_live_payout_gate(vaulted_circle, paypal_factory, collector):
    paypal = paypal_factory(payout_status="UNCLAIMED")
    state.set_flag(vaulted_circle, "ravi_declines", False)
    state.set_flag(vaulted_circle, "next_payout_unclaimed", True)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert res["ok"] is True and res["status"] == "UNCLAIMED"
    receiver = paypal.payouts[0]["items"][0]["receiver"]
    assert receiver.startswith("unclaimed-") and receiver.endswith("@example.com")
    board = state.board_model(vaulted_circle)
    assert (
        board["rounds"][2]["status"] == "COLLECTED" and board["rounds"][2]["payout"]["status"] == "UNCLAIMED"
    )
    assert board["circle"]["flags"]["next_payout_unclaimed"] is False  # one-shot flag
    again = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert again["ok"] is False and "live payout already exists" in again["denied"]
    status = await rounds.payout_status(vaulted_circle, 3)
    assert status["status"] == "UNCLAIMED" and paypal.item_polls[-1] == "FAKEITEM001"


async def test_pay_round_paypal_error_is_reported(vaulted_circle, paypal_factory, collector):
    paypal_factory(payout_error=True)
    state.set_flag(vaulted_circle, "ravi_declines", False)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert (
        res["ok"] is False
        and res["error"] == "INSUFFICIENT_FUNDS"
        and res["label"].startswith("Payout failed")
    )
    assert state.board_model(vaulted_circle)["rounds"][2]["payout"] is None


async def test_payout_status_for_replayed_round(vaulted_circle, paypal):
    status = await rounds.payout_status(vaulted_circle, 1)
    assert status["ok"] and status["status"] == "SUCCESS" and status["item_id"].startswith("REPLAY-")
    assert paypal.item_polls == []
    assert (await rounds.payout_status(vaulted_circle, 99))["ok"] is False


async def test_auth_error_with_mock_flag_is_not_a_sandbox_decline(
    vaulted_circle, paypal_factory, collector, monkeypatch
):
    """M1 review: a 401 on ravi's charge with ravi_declines on must not read as an injected decline."""
    from app.paypal.client import PayPalError

    paypal = paypal_factory()

    async def unauthorized(gw, **kw):
        raise PayPalError(
            401, {"error": "invalid_client", "error_description": "Client Authentication failed"}
        )

    monkeypatch.setattr(paypal, "charge_vaulted", unauthorized)
    paypal.install(monkeypatch)
    assert state.get_flags(state.load_circle(vaulted_circle)).get("ravi_declines")
    res = await rounds.charge_member(vaulted_circle, 3, "ravi", publish=collector)
    assert res["status"] == "PENDING" and res["decline_code"] is None
    assert res["label"] == "PayPal error: invalid_client" and res["label"] != MOCK_DECLINE_LABEL
    assert res["attempt"] == 0  # a call PayPal never evaluated does not consume an attempt


async def test_rate_limited_charge_does_not_consume_attempt(
    vaulted_circle, paypal_factory, collector, monkeypatch
):
    from app.paypal.client import PayPalError

    paypal = paypal_factory()

    async def throttled(gw, **kw):
        raise PayPalError(429, {"name": "RATE_LIMIT_REACHED", "details": [{"issue": "RATE_LIMIT_REACHED"}]})

    monkeypatch.setattr(paypal, "charge_vaulted", throttled)
    paypal.install(monkeypatch)
    res = await rounds.charge_member(vaulted_circle, 3, "asha", publish=collector)
    assert res["status"] == "PENDING" and res["attempt"] == 0


async def test_charge_without_paypal_credentials_burns_no_attempt(
    vaulted_circle, paypal, collector, monkeypatch
):
    from app.config import get_settings

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    get_settings.cache_clear()
    res = await rounds.charge_member(vaulted_circle, 3, "asha", publish=collector)
    assert res["ok"] is False and res["error"] == "PAYPAL_NOT_CONFIGURED" and res["attempt"] == 0
    assert paypal.charges == []
