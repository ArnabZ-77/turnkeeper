"""Internal MCP tools against the fake engine: shapes, error mapping, publisher binding."""

import fastmcp
import pytest
from engine_fakes import DEMO_CIRCLE, FakeEngine, PayPalError, PolicyDenied

from app.mcp import internal
from app.mcp.internal import bind_publisher, paypal_ops_internal


async def call(name: str, **args):
    async with fastmcp.Client(paypal_ops_internal) as c:
        result = await c.call_tool(name, args, raise_on_error=False)
    assert result.is_error is False, f"{name} raised to the model: {result.content}"
    assert isinstance(result.data, dict)
    assert list(result.data)[0] == "ok", "ok must be the first key"
    return result.data


async def test_get_circle_returns_board_with_wire_safe_money(fake_engine):
    data = await call("get_circle", circle_id=DEMO_CIRCLE)
    assert data["ok"] is True
    assert data["circle"]["amount"] == "200.00"
    assert data["circle"]["buffer_balance"] == "250.00"
    assert isinstance(data["circle"]["virtual_now"], str)
    assert {m["handle"] for m in data["members"]} == {"asha", "ravi", "meena", "tomas", "lin", "dev"}
    assert fake_engine.calls_named("board_model") == [{"circle_id": DEMO_CIRCLE}]


async def test_get_round_matrix_is_a_trimmed_subset(fake_engine):
    data = await call("get_round_matrix", circle_id=DEMO_CIRCLE)
    assert set(data) == {"ok", "members", "rounds", "contributions"}
    assert set(data["members"][0]) <= set(internal.MEMBER_KEYS)
    assert "email" not in data["members"][0]
    assert set(data["rounds"][0]) <= set(internal.ROUND_KEYS)
    assert len(data["contributions"]) == 12  # rounds 1-2 pre-settled for six members
    assert data["contributions"][0]["gross"] == "200.00"


async def test_get_member_history_derives_late_rounds_and_payday_rule(fake_engine):
    fake_engine.contributions[(2, "ravi")]["status"] = "DECLINED"
    fake_engine.contributions[(1, "ravi")]["attempt"] = 2
    data = await call("get_member_history", circle_id=DEMO_CIRCLE, member="ravi")
    assert data == {
        "ok": True,
        "member": "ravi",
        "late_rounds": [1, 2],
        "payday_rule": "every 2nd Friday",
        "commitments": [],
        "replies": [],
    }
    unknown = await call("get_member_history", circle_id=DEMO_CIRCLE, member="nobody")
    assert unknown["ok"] is False and unknown["error"] == "KeyError"


async def test_collect_round_wraps_results_and_queue_position(fake_engine_factory):
    engine = fake_engine_factory(declines=("ravi",))
    data = await call("collect_round", circle_id=DEMO_CIRCLE, round_n=3)
    assert data["ok"] is True
    assert data["queued_position"] == 0  # no gateway module yet -> 0, never an exception
    by_member = {r["member"]: r for r in data["results"]}
    assert len(by_member) == 6
    assert by_member["ravi"]["status"] == "DECLINED"
    assert by_member["ravi"]["decline_code"] == "INSTRUMENT_DECLINED"
    assert by_member["ravi"]["label"] == "API decline: sandbox-injected"
    assert by_member["asha"]["status"] == "COMPLETED" and by_member["asha"]["capture_id"] == "CAP-asha-3"
    assert engine.calls_named("collect_round") == [{"circle_id": DEMO_CIRCLE, "round_n": 3}]


async def test_policy_denied_becomes_ok_false_denied(fake_engine_factory):
    fake_engine_factory(charge_error=PolicyDenied("retries exhausted for ravi in round 3"))
    data = await call("charge_vaulted_member", circle_id=DEMO_CIRCLE, round_n=3, member="ravi")
    assert data == {"ok": False, "denied": "retries exhausted for ravi in round 3"}


async def test_paypal_error_becomes_ok_false_error_issues_label(fake_engine_factory):
    err = PayPalError(
        422,
        "UNPROCESSABLE_ENTITY",
        "The instrument presented was declined.",
        [{"issue": "INSTRUMENT_DECLINED"}],
    )
    fake_engine_factory(charge_error=err)
    data = await call("charge_vaulted_member", circle_id=DEMO_CIRCLE, round_n=3, member="ravi")
    assert data["ok"] is False
    assert data["error"] == "UNPROCESSABLE_ENTITY"
    assert data["issues"] == ["INSTRUMENT_DECLINED"]
    assert data["label"] == "API decline: INSTRUMENT_DECLINED"
    assert data["debug_id"] == "dbg-test"


async def test_paypal_error_without_issues_gets_descriptive_label(fake_engine_factory):
    fake_engine_factory(pay_error=PayPalError(429, "RATE_LIMIT_REACHED", "Too many requests"))
    data = await call("create_pot_payout", circle_id=DEMO_CIRCLE, round_n=3)
    assert data["ok"] is False and data["error"] == "RATE_LIMIT_REACHED" and data["issues"] == []
    assert data["label"] == "PayPal 429 RATE_LIMIT_REACHED: Too many requests"


async def test_unexpected_exception_never_reaches_the_model(fake_engine_factory):
    fake_engine_factory(pay_error=RuntimeError("db locked"))
    data = await call("create_pot_payout", circle_id=DEMO_CIRCLE, round_n=3)
    from app.engine.common import GENERIC_ERROR_TEXT

    assert data == {"ok": False, "error": "RuntimeError", "message": GENERIC_ERROR_TEXT}
    assert "db locked" not in str(data)


def test_failure_dict_matches_by_class_name_when_modules_are_missing(monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "app.domain.policy", raising=False)
    monkeypatch.delitem(sys.modules, "app.paypal.client", raising=False)

    class PolicyDenied(Exception):  # a stand-in with the contract's name and attribute
        reason = "frozen circle"

    class PayPalError(Exception):
        status, name, message, details, debug_id = 500, "INTERNAL_SERVICE_ERROR", "boom", [], None
        issue_codes = []

    assert internal.failure_dict(PolicyDenied()) == {"ok": False, "denied": "frozen circle"}
    out = internal.failure_dict(PayPalError())
    assert (
        out["error"] == "INTERNAL_SERVICE_ERROR" and out["label"] == "PayPal 500 INTERNAL_SERVICE_ERROR: boom"
    )


async def test_engine_missing_is_reported_not_raised(monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "app.engine.rounds", raising=False)
    monkeypatch.setattr(
        internal,
        "_rounds",
        lambda: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'app.engine'")),
    )
    data = await call("get_available_balance", circle_id=DEMO_CIRCLE)
    assert data["ok"] is False and data["error"] == "ENGINE_UNAVAILABLE"


@pytest.mark.parametrize(
    "name, args",
    [
        ("get_circle", {"circle_id": "ABC123"}),
        ("collect_round", {"circle_id": DEMO_CIRCLE, "round_n": 0}),
        ("charge_vaulted_member", {"circle_id": DEMO_CIRCLE, "round_n": 3, "member": "Ravi Kumar"}),
        ("get_order_details", {"circle_id": "toolong1", "member": "ravi", "round_n": 3}),
        ("record_note", {"circle_id": DEMO_CIRCLE, "text": "   "}),
    ],
)
async def test_invalid_handles_are_rejected_before_touching_the_engine(fake_engine, name, args):
    data = await call(name, **args)
    assert data["ok"] is False and data["error"] == "INVALID_INPUT"
    assert fake_engine.calls == []


async def test_bind_publisher_routes_engine_events_to_the_session(fake_engine):
    seen = []

    async def publish(event):
        seen.append(event)

    with bind_publisher(DEMO_CIRCLE, publish):
        data = await call("collect_round", circle_id=DEMO_CIRCLE, round_n=3)
        assert data["ok"] is True
        note = await call("record_note", circle_id=DEMO_CIRCLE, text="Round 3: five captured, ravi declined.")
    assert note == {"ok": True, "chars": 38}
    chips = [e for e in seen if e["type"] == "chip"]
    assert len(chips) == 6
    notes = [e for e in seen if e["type"] == "trace"]
    assert notes == [
        {**notes[0], "kind": "text", "text": "Round 3: five captured, ravi declined.", "source": "note"}
    ]
    assert "ts" in notes[0]
    # outside the binding, events go to the no-op publisher and nothing breaks
    assert internal.publisher_for(DEMO_CIRCLE) is internal._noop_publish
    again = await call("collect_round", circle_id=DEMO_CIRCLE, round_n=3)
    assert again["ok"] is True and len(seen) == 7


async def test_confirm_balance_payout_status_sequence(fake_engine):
    assert (await call("collect_round", circle_id=DEMO_CIRCLE, round_n=3))["ok"]
    details = await call("get_order_details", circle_id=DEMO_CIRCLE, member="asha", round_n=3)
    assert details["ok"] is True and details["capture_id"] == "CAP-asha-3"
    assert fake_engine.calls_named("confirm_capture") == [
        {"circle_id": DEMO_CIRCLE, "round_n": 3, "member": "asha"}
    ]
    balance = await call("get_available_balance", circle_id=DEMO_CIRCLE)
    assert balance == {
        "ok": True,
        "paypal_available": "900.00",
        "circle_available": "1405.18",
        "source": "balances",
    }
    payout = await call("create_pot_payout", circle_id=DEMO_CIRCLE, round_n=3)
    assert payout["ok"] is True and payout["amount"] == "1155.18" and payout["recipient"] == "dev"
    status = await call("get_payout_status", circle_id=DEMO_CIRCLE, round_n=3)
    assert status == {"ok": True, "status": "SUCCESS", "item_id": "ITEM-3", "batch_id": "BATCH-3"}


def test_jsonable_formats_decimals_dates_and_sets():
    import datetime as dt
    from decimal import Decimal

    out = internal.jsonable(
        {"a": Decimal("200"), "b": Decimal("7.4"), "c": dt.date(2026, 11, 1), "d": {"x", "y"}}
    )
    assert out == {"a": "200.00", "b": "7.40", "c": "2026-11-01", "d": ["x", "y"]}


def test_fake_engine_is_self_consistent():
    engine = FakeEngine()
    assert len(engine.contributions) == 12
    assert engine.board_model(DEMO_CIRCLE)["circle"]["is_fixture_clone"] is True
