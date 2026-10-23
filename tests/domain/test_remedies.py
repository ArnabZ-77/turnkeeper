"""app.domain.remedies: remedy-set validation (allow and deny paths) and the rules-mode default proposal."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.charter import charter_sha256
from app.domain.remedies import (
    Commitment,
    Remedy,
    RemedyParams,
    RemedySet,
    next_weekday,
    one_net_contribution,
    propose_default_remedies,
    swap_partner_for,
    validate_remedy_set,
)

NOW = datetime(2026, 12, 29, 9, 0, tzinfo=UTC)  # a Tuesday; the next Friday is 2027-01-01
FRIDAY = date(2027, 1, 1)
SWAPPABLE = ["meena", "asha", "dev", "lin", "ravi", "tomas"]


@pytest.fixture
def short_state(make_state):
    """Round 3, ravi declined once, everyone else paid."""

    def _make(for_charter=None, **overrides):
        base = {"now": NOW}
        c = for_charter
        contributions = {h: "COMPLETED" for h in ["asha", "ravi", "meena", "tomas", "lin", "dev"]}
        contributions["ravi"] = "DECLINED"
        base["contributions"] = contributions
        base.update(overrides)
        return make_state(c, **base)

    return _make


def _set(charter, *remedies: dict, member="ravi", round_n=3, sha=None):
    h = sha or charter_sha256(charter)
    items = [
        Remedy(
            type=r["type"],
            member=r.get("member", member),
            round_n=r.get("round_n", round_n),
            params=RemedyParams(**r.get("params", {})),
            reason="test",
            charter_sha256=r.get("sha", h),
        )
        for r in remedies
    ]
    return RemedySet(member=member, round_n=round_n, remedies=items, charter_sha256=h)


ADVANCE = {"type": "buffer_advance", "params": {"amount": "192.53"}}
INVOICE = {"type": "repay_by_invoice", "params": {"amount": "200.00", "due_date": FRIDAY}}


# --- helpers ------------------------------------------------------------------------------------------


def test_next_weekday_is_strictly_after():
    assert next_weekday(date(2026, 12, 29)) == FRIDAY
    assert next_weekday(FRIDAY) == date(2027, 1, 8)


def test_one_net_contribution(charter):
    assert one_net_contribution(charter) == Decimal("192.53")


# --- default proposal ---------------------------------------------------------------------------------


def test_default_when_ravi_is_last_has_no_swap_but_is_legal(charter, short_state):
    state = short_state()
    rs = propose_default_remedies(charter, state, "ravi")
    assert [r.type for r in rs.remedies] == ["buffer_advance", "repay_by_invoice"]
    assert rs.remedies[0].params.amount == Decimal("192.53")
    assert rs.remedies[1].params.amount == Decimal("200.00")
    assert rs.remedies[1].params.due_date == FRIDAY
    assert all(r.reason for r in rs.remedies)
    assert validate_remedy_set(charter, state, rs) == []


def test_default_moves_ravi_later_with_the_next_member(make_charter, short_state):
    c = make_charter(rotation=SWAPPABLE)
    state = short_state(c)
    assert swap_partner_for(c, state, "ravi") == "tomas"
    rs = propose_default_remedies(c, state, "ravi")
    assert [r.type for r in rs.remedies] == ["buffer_advance", "repay_by_invoice", "turn_swap"]
    swap = rs.of_type("turn_swap")[0]
    assert swap.params.swap_with == "tomas" and "round 5 -> 6" in swap.reason
    assert rs.affected_members() == ["ravi", "tomas"]
    assert validate_remedy_set(c, state, rs) == []
    problems = validate_remedy_set(c, state, rs, consents={"ravi"})
    assert any("missing: tomas" in p for p in problems)
    assert validate_remedy_set(c, state, rs, consents={"ravi", "tomas"}) == []


def test_default_uses_the_commitment_date(charter, short_state):
    later = date(2027, 1, 8)
    rs = propose_default_remedies(
        charter, short_state(), "ravi", Commitment(member="ravi", amount="200.00", date=later)
    )
    assert rs.of_type("repay_by_invoice")[0].params.due_date == later


def test_default_with_a_small_buffer_advances_what_is_there(charter, short_state):
    state = short_state(buffer_balance="100.00")
    rs = propose_default_remedies(charter, state, "ravi")
    assert rs.of_type("buffer_advance")[0].params.amount == Decimal("100.00")
    assert validate_remedy_set(charter, state, rs) == []


def test_default_with_an_empty_buffer_falls_back_to_a_retry(charter, short_state):
    state = short_state(buffer_balance="0.00")
    rs = propose_default_remedies(charter, state, "ravi")
    assert [r.type for r in rs.remedies] == ["retry_on_date"]
    assert rs.remedies[0].params.due_date == FRIDAY
    assert validate_remedy_set(charter, state, rs) == []


# --- deny paths ---------------------------------------------------------------------------------------


def test_type_outside_the_remedy_menu(make_charter, short_state):
    c = make_charter(remedy_menu=["buffer_advance", "turn_swap"])
    problems = validate_remedy_set(c, short_state(c), _set(c, ADVANCE, INVOICE))
    assert any("repay_by_invoice is not in the charter's remedy menu" in p for p in problems)


@pytest.mark.parametrize(
    ("amount", "overrides", "needle"),
    [
        ("192.53", {"buffer_balance": "100.00"}, "exceeds the buffer balance"),
        ("199.00", {}, "exceeds one net contribution"),
        ("50.00", {"open_advances": {"ravi": "20.00"}}, "already has an open advance"),
        ("0.00", {}, "needs a positive amount"),
    ],
)
def test_advance_limits(charter, short_state, amount, overrides, needle):
    rs = _set(charter, {"type": "buffer_advance", "params": {"amount": amount}})
    problems = validate_remedy_set(charter, short_state(**overrides), rs)
    assert any(needle in p for p in problems), problems


def test_invoice_requires_an_advance_in_the_same_set(charter, short_state):
    retry = {"type": "retry_on_date", "params": {"due_date": FRIDAY}}
    problems = validate_remedy_set(charter, short_state(), _set(charter, retry, INVOICE))
    assert any("needs a buffer_advance for ravi in the same set" in p for p in problems)


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({"amount": "250.00", "due_date": FRIDAY}, "exceeds one contribution"),
        ({"amount": "200.00", "due_date": date(2026, 12, 1)}, "is in the past"),
        ({"amount": "200.00"}, "needs a due_date"),
    ],
)
def test_invoice_limits(charter, short_state, params, needle):
    rs = _set(charter, ADVANCE, {"type": "repay_by_invoice", "params": params})
    assert any(needle in p for p in validate_remedy_set(charter, short_state(), rs))


def test_short_member_may_only_move_later(make_charter, short_state):
    c = make_charter(rotation=SWAPPABLE)
    rs = _set(c, ADVANCE, {"type": "turn_swap", "params": {"swap_with": "lin"}})  # round 5 -> 4
    problems = validate_remedy_set(c, short_state(c), rs)
    assert any("may only move later" in p for p in problems)


def test_swap_cannot_touch_a_paid_turn(make_charter, short_state):
    c = make_charter(rotation=SWAPPABLE)
    rs = _set(c, ADVANCE, {"type": "turn_swap", "params": {"swap_with": "meena"}})
    problems = validate_remedy_set(c, short_state(c), rs)
    assert any("already paid" in p for p in problems)


def test_swap_keeps_the_collateral_rule(charter, make_state):
    # Round 2: asha (track record) is short; swapping with dev (none) would put dev before asha.
    state = make_state(
        round_n=2,
        now=NOW,
        contributions={
            h: ("DECLINED" if h == "asha" else "COMPLETED")
            for h in ["asha", "ravi", "meena", "tomas", "lin", "dev"]
        },
    )
    rs = _set(
        charter, ADVANCE, {"type": "turn_swap", "params": {"swap_with": "dev"}}, member="asha", round_n=2
    )
    problems = validate_remedy_set(charter, state, rs)
    assert any("collateral rule forbids it" in p for p in problems)


def test_one_swap_per_member(make_charter, short_state):
    c = make_charter(rotation=SWAPPABLE)
    rs = _set(c, ADVANCE, {"type": "turn_swap", "params": {"swap_with": "tomas"}})
    problems = validate_remedy_set(c, short_state(c, swaps_done={"tomas"}), rs)
    assert any("already swapped once" in p for p in problems)
    assert swap_partner_for(c, short_state(c, swaps_done={"ravi"}), "ravi") is None


def test_hash_mismatch_is_rejected(charter, short_state):
    rs = _set(charter, ADVANCE, sha="0" * 64)
    problems = validate_remedy_set(charter, short_state(), rs)
    assert any("proposed for charter 00000000" in p for p in problems)


def test_a_set_must_cover_the_shortfall(make_charter, short_state):
    c = make_charter(rotation=SWAPPABLE)
    rs = _set(c, {"type": "turn_swap", "params": {"swap_with": "tomas"}})
    assert any("does not cover the shortfall" in p for p in validate_remedy_set(c, short_state(c), rs))


def test_no_shortfall_no_remedy(charter, make_state):
    rs = _set(charter, ADVANCE)
    problems = validate_remedy_set(charter, make_state(now=NOW), rs)
    assert any("no shortfall to remedy" in p for p in problems)


def test_frozen_circle_and_duplicates(charter, short_state):
    rs = _set(charter, ADVANCE, ADVANCE)
    problems = validate_remedy_set(charter, short_state(frozen=True), rs)
    assert any("frozen" in p for p in problems)
    assert any("at most one buffer_advance per set" in p for p in problems)


def test_retry_rules(charter, short_state):
    ok = _set(charter, {"type": "retry_on_date", "params": {"due_date": FRIDAY}})
    assert validate_remedy_set(charter, short_state(), ok) == []
    too_soon = _set(charter, {"type": "retry_on_date", "params": {"due_date": date(2026, 12, 29)}})
    assert any("must be after today" in p for p in validate_remedy_set(charter, short_state(), too_soon))
    never = short_state(decline_codes={"ravi": "PAYER_ACTION_REQUIRED"})
    assert any("never retried" in p for p in validate_remedy_set(charter, never, ok))
    spent = short_state(attempts={"ravi": 3})
    assert any("no retries left" in p for p in validate_remedy_set(charter, spent, ok))
