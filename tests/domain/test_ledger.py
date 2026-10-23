from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.ledger import LedgerEntry, LedgerView, buffer_balance, circle_available, round_net_collected

NET = "192.53"


def view(*entries: dict) -> LedgerView:
    return LedgerView(entries=[LedgerEntry(**e) for e in entries])


def contribution(member: str, round_n: int = 3) -> dict:
    return {
        "kind": "contribution_net",
        "amount": NET,
        "round_n": round_n,
        "member": member,
        "ref": f"cap-{member}",
    }


def test_empty_view_is_all_zero():
    v = LedgerView()
    assert circle_available(v) == Decimal("0.00")
    assert buffer_balance(v) == Decimal("0.00")
    assert round_net_collected(v, 3) == Decimal("0.00")
    assert circle_available(v).as_tuple().exponent == -2


def test_circle_available_formula():
    v = view(
        contribution("asha"),
        contribution("meena"),
        {"kind": "buffer_seed", "amount": "250.00"},
        {"kind": "buffer_advance", "amount": NET, "round_n": 3, "member": "ravi"},
        {"kind": "refund", "amount": "100.00", "round_n": 2, "ref": "payout-item-cancelled"},
        {"kind": "payout", "amount": "400.00", "round_n": 2, "member": "asha"},
        {"kind": "payout_fee", "amount": "0.25", "round_n": 2},
        {"kind": "capture_refund", "amount": "50.00", "round_n": 1, "member": "lin"},
        {"kind": "advance_repaid", "amount": NET, "round_n": 3, "member": "ravi"},
    )
    # 192.53 * 3 + 100.00 - 400.00 - 0.25 - 50.00
    assert circle_available(v) == Decimal("227.34")


def test_buffer_balance_formula():
    v = view(
        {"kind": "buffer_seed", "amount": "250.00"},
        {"kind": "buffer_advance", "amount": NET, "round_n": 3, "member": "ravi"},
        {"kind": "advance_repaid", "amount": "100.00", "round_n": 3, "member": "ravi"},
        contribution("asha"),  # contributions never touch the buffer
    )
    assert buffer_balance(v) == Decimal("157.47")


def test_round_net_collected_filters_by_round_and_kind():
    v = view(
        contribution("asha", 2),
        contribution("asha", 3),
        contribution("meena", 3),
        {"kind": "buffer_advance", "amount": NET, "round_n": 3, "member": "ravi"},  # not a contribution
    )
    assert round_net_collected(v, 3) == Decimal("385.06")
    assert round_net_collected(v, 2) == Decimal("192.53")
    assert round_net_collected(v, 1) == Decimal("0.00")


def test_advance_moves_money_from_buffer_to_circle_and_repayment_restores_the_buffer():
    seed = {"kind": "buffer_seed", "amount": "250.00"}
    before = view(seed, *(contribution(h) for h in ("asha", "meena", "dev", "lin", "tomas")))
    advance = {"kind": "buffer_advance", "amount": NET, "round_n": 3, "member": "ravi"}
    after = view(seed, *(contribution(h) for h in ("asha", "meena", "dev", "lin", "tomas")), advance)
    assert circle_available(after) == circle_available(before) + Decimal(NET)
    assert buffer_balance(after) == Decimal("250.00") - Decimal(NET)
    assert circle_available(after) + buffer_balance(after) == circle_available(before) + buffer_balance(
        before
    )

    repayment = LedgerEntry(kind="advance_repaid", amount=NET, round_n=3, member="ravi")
    repaid = LedgerView(entries=[*after.entries, repayment])
    assert buffer_balance(repaid) == Decimal("250.00")
    assert circle_available(repaid) == circle_available(after)


def test_entry_rejects_negative_amount_and_unknown_kind():
    with pytest.raises(ValidationError):
        LedgerEntry(kind="payout", amount="-1.00")
    with pytest.raises(ValidationError):
        LedgerEntry(kind="tip", amount="1.00")
    assert LedgerEntry(kind="payout", amount="5").amount == Decimal("5.00")
