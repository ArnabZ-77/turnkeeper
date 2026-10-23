"""BoardModel shape, CircleState snapshot, member history and the gateway call log."""

from decimal import Decimal

import pytest

from app.domain.policy import CircleState
from app.engine import state
from app.models import PayPalCall

BOARD_KEYS = {"circle", "members", "rounds", "contributions", "counters", "ticker"}
CIRCLE_KEYS = {
    "id",
    "name",
    "amount",
    "currency",
    "cadence",
    "charter_sha256",
    "fee_policy",
    "buffer_balance",
    "circle_available",
    "status",
    "virtual_now",
    "run_id",
    "rotation",
    "mandate_present",
    "flags",
}
MEMBER_KEYS = {"handle", "name", "lang", "payout_wallet", "vaulted", "rotation_pos"}
ROUND_KEYS = {"n", "due_at", "recipient", "status", "payout", "paid_at"}
CONTRIBUTION_KEYS = {
    "round_n",
    "member",
    "status",
    "order_id",
    "capture_id",
    "gross",
    "fee",
    "net",
    "decline_code",
    "decline_source",
    "attempt",
    "webhook_verified",
    "predicted",
    "label",
}


def test_board_model_shape(circle_id):
    board = state.board_model(circle_id)
    assert set(board) == BOARD_KEYS
    circle = board["circle"]
    assert CIRCLE_KEYS <= set(circle)
    assert circle["id"] == circle_id and circle["amount"] == "200.00" and circle["currency"] == "USD"
    assert circle["buffer_balance"] == "250.00" and circle["circle_available"] == "0.00"
    assert circle["mandate_present"] is True
    assert circle["flags"] == {"ravi_declines": True, "next_payout_unclaimed": False}
    assert circle["rotation"] == ["meena", "asha", "dev", "lin", "tomas", "ravi"]
    assert circle["virtual_now"].endswith("Z")

    assert len(board["members"]) == 6
    assert all(set(m) == MEMBER_KEYS for m in board["members"])
    assert [m["handle"] for m in board["members"]] == circle["rotation"]
    assert all(m["vaulted"] is False for m in board["members"])
    assert all("email" not in m and "sandbox_account" not in m for m in board["members"])

    assert [r["n"] for r in board["rounds"]] == [1, 2, 3, 4, 5, 6]
    assert all(set(r) == ROUND_KEYS for r in board["rounds"])
    r1 = board["rounds"][0]
    assert r1["status"] == "PAID" and set(r1["payout"]) == {"batch_id", "item_id", "status", "amount"}
    assert r1["payout"]["status"] == "SUCCESS" and r1["paid_at"].endswith("Z")
    assert board["rounds"][2]["payout"] is None and board["rounds"][2]["status"] == "PENDING"

    assert len(board["contributions"]) == 36
    assert all(set(c) == CONTRIBUTION_KEYS for c in board["contributions"])
    statuses = {c["status"] for c in board["contributions"]}
    assert statuses <= {"PENDING", "COMPLETED", "DECLINED", "COVERED"}

    assert set(board["counters"]) == {"organizer_messages_typed", "rounds_paid_on_time", "shortfalls_covered"}
    assert set(board["ticker"]) == {"last_capture_at", "paid_at", "seconds"}
    assert board["ticker"]["seconds"] == 42  # replay payout lands 42 s after the last capture


def test_board_model_unknown_circle():
    with pytest.raises(LookupError):
        state.board_model("zzzzzz")


def test_circle_state_snapshot(circle_id):
    cs = state.circle_state(circle_id, 3)
    assert isinstance(cs, CircleState)
    assert cs.round_n == 3 and cs.run_id == state.load_circle(circle_id).run_id
    assert set(cs.contributions) == {"asha", "ravi", "meena", "tomas", "lin", "dev"}
    assert set(cs.contributions.values()) == {"PENDING"}
    assert cs.buffer_balance == Decimal("250.00") and cs.circle_available == Decimal("0.00")
    assert cs.paypal_available is None and cs.live_payout_exists is False
    assert cs.mandate_ok is True and cs.frozen is False
    assert cs.revoked == set() and cs.rotation_override is None
    settled = state.circle_state(circle_id, 1)
    assert set(settled.contributions.values()) == {"COMPLETED"}
    assert settled.live_payout_exists is True


def test_flags_and_counters_roundtrip(circle_id):
    assert state.set_flag(circle_id, "ravi_declines", False)["ravi_declines"] is False
    assert state.get_flags(state.load_circle(circle_id))["ravi_declines"] is False
    with pytest.raises(ValueError):
        state.set_flag(circle_id, "nope", True)
    assert state.bump_counter(circle_id, "shortfalls_covered")["shortfalls_covered"] == 1
    with pytest.raises(ValueError):
        state.bump_counter(circle_id, "nope")


def test_member_history_uses_fixture_history(circle_id):
    hist = state.member_history(circle_id, "ravi")
    assert hist["late_rounds"] == [1, 2]
    assert hist["payday_rule"] == "every 2nd Friday"
    assert hist["replies"] and hist["commitments"] == []
    assert state.member_history(circle_id, "asha")["late_rounds"] == []
    with pytest.raises(KeyError):
        state.member_history(circle_id, "nobody")


def test_ledger_view_matches_domain(circle_id):
    from app.domain.ledger import buffer_balance, circle_available

    view = state.ledger_view(circle_id)
    assert circle_available(view) == Decimal("0.00")
    assert buffer_balance(view) == Decimal("250.00")


def test_record_paypal_call_persists_and_never_raises():
    from sqlmodel import select

    from app.db import session_scope

    state.record_paypal_call(
        {
            "ts": 1_700_000_000,
            "method": "POST",
            "path": "/v2/checkout/orders",
            "status": 201,
            "request_id": "tk:x",
            "ms": 120,
        }
    )
    state.record_paypal_call(
        {"ts": "2026-10-23T10:00:00Z", "method": "GET", "path": "/x", "status": None, "ms": None}
    )
    state.record_paypal_call({"status": "not-an-int"})  # swallowed
    with session_scope() as db:
        rows = db.exec(select(PayPalCall)).all()
    assert len(rows) == 2 and rows[0].status == 201 and rows[0].ms == 120
