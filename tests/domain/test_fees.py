from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from app.domain.fees import Money, NonNegativeMoney, estimate_fee, money, net_of, pot_amount, wire_amount


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.01"),
        ("1.004", "1.00"),
        ("2.675", "2.68"),
        ("1.995", "2.00"),
        ("-0.005", "-0.01"),
        (2, "2.00"),
        (Decimal("1E+2"), "100.00"),
        (0.1, "0.10"),  # floats are tolerated through their shortest repr, never produced
    ],
)
def test_money_rounds_half_up_to_two_places(raw, expected):
    result = money(raw)
    assert result == Decimal(expected)
    assert result.as_tuple().exponent == -2


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "abc", None, True])
def test_money_rejects_junk(raw):
    with pytest.raises(ValueError):
        money(raw)


def test_wire_amount_is_never_scientific():
    assert wire_amount(Decimal("1E+3")) == "1000.00"
    assert wire_amount("7") == "7.00"


def test_estimate_fee_default_rate():
    assert estimate_fee(Decimal("200.00")) == Decimal("7.47")  # 6.98 + 0.49
    assert estimate_fee("10.00") == Decimal("0.84")  # 0.349 + 0.49 = 0.839 -> 0.84
    assert estimate_fee(0) == Decimal("0.00")


def test_estimate_fee_custom_rate():
    assert estimate_fee("100.00", pct=Decimal("0.029"), fixed=Decimal("0.30")) == Decimal("3.20")


def test_net_of():
    assert net_of("200.00", "7.47") == Decimal("192.53")
    with pytest.raises(ValueError, match="exceeds gross"):
        net_of("0.10", "0.49")


def test_pot_amount_net_pot(charter):
    nets = [Decimal("192.53")] * 5
    assert pot_amount(charter, nets, Decimal("192.53"), Decimal("0.25")) == Decimal("1154.93")
    assert pot_amount(charter, nets, "0.00", "0.00") == Decimal("962.65")


def test_pot_amount_never_negative(charter):
    assert pot_amount(charter, [], "0.00", "0.25") == Decimal("0.00")


@pytest.mark.parametrize("policy", ["gross_up", "organizer_absorbs"])
def test_pot_amount_gross_policies_pay_the_gross_value(make_charter, policy):
    c = make_charter(fee_policy=policy)
    assert pot_amount(c, ["192.53"] * 5, "192.53", "0.25") == Decimal("1192.53")


def test_pot_amount_rejects_negative_inputs(charter):
    with pytest.raises(ValueError):
        pot_amount(charter, ["-1.00"], "0.00", "0.00")
    with pytest.raises(ValueError):
        pot_amount(charter, [], "-1.00", "0.00")


def test_money_field_type_normalizes_and_rejects_sub_cent():
    class M(BaseModel):
        v: Money
        w: NonNegativeMoney = Decimal("0.00")

    assert M(v="5").v == Decimal("5.00")
    assert M.model_validate_json('{"v": 5.0}').v == Decimal("5.00")
    with pytest.raises(ValidationError):
        M(v="5.005")
    with pytest.raises(ValidationError):
        M(v="1.00", w="-0.01")
