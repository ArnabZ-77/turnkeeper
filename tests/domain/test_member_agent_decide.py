"""The member agent's deterministic decision table (pure: no DB, no clock, no model)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain import member_mandate as mm
from app.domain import remedies as rd
from app.domain.charter import charter_sha256
from app.engine.member_agent import AgentRequest, decide

SECRET = "unit-test-only-secret"  # a test fixture value, not a real secret
SWAPPABLE = ["meena", "asha", "dev", "lin", "ravi", "tomas"]  # ravi round 5, tomas round 6
APPROVED = datetime(2026, 10, 28, tzinfo=UTC)


@pytest.fixture
def ch(make_charter):
    return make_charter(rotation=SWAPPABLE)


@pytest.fixture
def st(ch, make_state):
    handles = [m.handle for m in ch.members]
    contributions = {h: "COMPLETED" for h in handles} | {"ravi": "DECLINED"}
    return make_state(
        ch, contributions=contributions, attempts={"ravi": 1}, decline_codes={"ravi": "INSTRUMENT_DECLINED"}
    )


def _mandate(ch, member, **over) -> mm.MemberMandate:
    base = mm.default_member_mandate(
        ch,
        member,
        circle_id="abc123",
        charter_sha256=charter_sha256(ch),
        approved_at=APPROVED,
        expires=date(2027, 6, 1),
    )
    return mm.sign_member_mandate(base.model_copy(update=over), SECRET)


def _remedy(ch, kind, **params) -> rd.Remedy:
    return rd.Remedy(
        type=kind,
        member="ravi",
        round_n=3,
        params=rd.RemedyParams(**params),
        charter_sha256=charter_sha256(ch),
    )


def _set(ch, *remedies) -> rd.RemedySet:
    return rd.RemedySet(member="ravi", round_n=3, remedies=list(remedies), charter_sha256=charter_sha256(ch))


@pytest.fixture
def full_set(ch):
    return _set(
        ch,
        _remedy(ch, "buffer_advance", amount=Decimal("150.00")),
        _remedy(ch, "repay_by_invoice", amount=Decimal("150.00"), due_date=date(2027, 1, 8)),
        _remedy(ch, "turn_swap", swap_with="tomas"),
    )


@pytest.fixture
def advance_only(ch):
    return _set(ch, _remedy(ch, "buffer_advance", amount=Decimal("150.00")))


def _ask(kind, member, rs):
    return AgentRequest(kind, member, rs)


def test_partner_moving_earlier_says_yes(ch, st, full_set):
    d = decide(_ask("consent", "tomas", full_set), _mandate(ch, "tomas"), ch, st)
    assert (d.choice, d.rule) == ("yes", "swap_earlier")
    assert d.reason == "rule: swap moves you earlier, shortfall covered"
    assert any("round 6 -> 5" in t for t in d.trace)


def test_short_member_moving_later_asks(ch, st, full_set):
    d = decide(_ask("consent", "ravi", full_set), _mandate(ch, "ravi"), ch, st)
    assert (d.choice, d.rule) == ("ask", "swap_later_ask")
    assert "round 5 to round 6" in d.reason


def test_later_swap_is_accepted_when_the_rule_is_off(ch, st, full_set):
    m = _mandate(ch, "ravi", require_ask_for_later_swap=False)
    d = decide(_ask("vote", "ravi", full_set), m, ch, st)
    assert (d.choice, d.rule) == ("yes", "swap_allowed")


def test_earlier_swap_asks_when_the_member_wants_to_be_asked(ch, st, full_set):
    m = _mandate(ch, "tomas", accept_earlier_swap=False)
    d = decide(_ask("consent", "tomas", full_set), m, ch, st)
    assert (d.choice, d.rule) == ("ask", "swap_earlier_ask")


def test_bystander_vote_with_a_covered_legal_set_is_auto_yes(ch, st, full_set):
    d = decide(_ask("vote", "asha", full_set), _mandate(ch, "asha"), ch, st)
    assert (d.choice, d.rule) == ("yes", "auto_legal_covered")
    assert d.reason == "rule: charter-legal and shortfall covered"


def test_bystander_vote_without_a_swap_is_auto_yes(ch, st, advance_only):
    d = decide(_ask("vote", "meena", advance_only), _mandate(ch, "meena"), ch, st)
    assert (d.choice, d.rule) == ("yes", "auto_legal_covered")


def test_advance_over_the_cap_is_no(ch, st, advance_only):
    m = _mandate(ch, "meena", max_advance=Decimal("100.00"))
    d = decide(_ask("vote", "meena", advance_only), m, ch, st)
    assert (d.choice, d.rule) == ("no", "advance_over_cap")
    assert "150.00" in d.reason and "100.00" in d.reason


def test_advance_at_the_cap_is_fine(ch, st, advance_only):
    m = _mandate(ch, "meena", max_advance=Decimal("150.00"))
    assert decide(_ask("vote", "meena", advance_only), m, ch, st).choice == "yes"


def test_a_zero_advance_cap_means_never(ch, st, advance_only):
    m = _mandate(ch, "meena", max_advance=Decimal("0"))
    assert decide(_ask("vote", "meena", advance_only), m, ch, st).rule == "advance_over_cap"


def test_not_covered_is_no(ch, st):
    rs = _set(ch, _remedy(ch, "turn_swap", swap_with="tomas"))
    d = decide(_ask("vote", "asha", rs), _mandate(ch, "asha"), ch, st)
    assert d.choice == "no" and d.rule in ("not_legal", "not_covered")


def test_not_charter_legal_is_no(ch, st):
    rs = _set(ch, _remedy(ch, "buffer_advance", amount=Decimal("900.00")))
    d = decide(_ask("vote", "asha", rs), _mandate(ch, "asha"), ch, st)
    assert (d.choice, d.rule) == ("no", "not_legal")
    assert d.reason.startswith("not charter-legal:")


def test_a_swap_to_an_earlier_turn_by_the_short_member_is_not_legal(ch, st):
    rs = _set(
        ch,
        _remedy(ch, "buffer_advance", amount=Decimal("150.00")),
        _remedy(ch, "turn_swap", swap_with="lin"),  # lin is round 4: earlier than ravi's round 5
    )
    d = decide(_ask("consent", "lin", rs), _mandate(ch, "lin"), ch, st)
    assert (d.choice, d.rule) == ("no", "not_legal")


def test_no_mandate_asks_the_human(ch, st, full_set):
    d = decide(_ask("vote", "asha", full_set), None, ch, st)
    assert (d.choice, d.rule) == ("ask", "no_mandate")


def test_expired_mandate_asks_the_human(ch, st, full_set):
    old = _mandate(ch, "asha", expires=date(2026, 1, 1))
    st2 = st.model_copy(update={"now": datetime(2026, 12, 1, tzinfo=UTC)})
    d = decide(_ask("vote", "asha", full_set), old, ch, st2)
    assert (d.choice, d.rule) == ("ask", "no_mandate") and "expired" in d.reason


def test_mandate_for_another_charter_asks_the_human(ch, st, full_set):
    other = _mandate(ch, "asha", charter_sha256="d" * 64)
    d = decide(_ask("vote", "asha", full_set), other, ch, st)
    assert d.rule == "no_mandate" and "charter" in d.reason


def test_mandate_for_another_member_asks_the_human(ch, st, full_set):
    d = decide(_ask("vote", "asha", full_set), _mandate(ch, "meena"), ch, st)
    assert d.rule == "no_mandate" and "meena" in d.reason


def test_failed_signature_check_in_state_asks_the_human(ch, st, full_set):
    bad = st.model_copy(
        update={
            "member_mandate_ok": {"asha": {"ok": False, "reason": "asha mandate signature does not verify"}}
        }
    )
    d = decide(_ask("vote", "asha", full_set), _mandate(ch, "asha"), ch, bad)
    assert d.rule == "no_mandate" and "does not verify" in d.reason


def test_a_valid_signature_check_in_state_lets_it_decide(ch, st, full_set):
    good = st.model_copy(
        update={"member_mandate_ok": {"asha": {"ok": True, "reason": "ok", "max_charge_per_round": "200.00"}}}
    )
    assert decide(_ask("vote", "asha", full_set), _mandate(ch, "asha"), ch, good).choice == "yes"


def test_decide_is_deterministic_and_has_a_trace(ch, st, full_set):
    req, m = _ask("consent", "tomas", full_set), _mandate(ch, "tomas")
    a, b = decide(req, m, ch, st), decide(req, m, ch, st)
    assert a == b and len(a.trace) >= 3


def test_expiry_is_judged_against_the_state_clock(ch, st, full_set):
    m = _mandate(ch, "asha", expires=date(2026, 12, 1))
    on_last_day = st.model_copy(update={"now": datetime(2026, 12, 1, 12, tzinfo=UTC)})
    day_after = st.model_copy(update={"now": datetime(2026, 12, 1, 12, tzinfo=UTC) + timedelta(days=1)})
    assert decide(_ask("vote", "asha", full_set), m, ch, on_last_day).choice == "yes"
    assert decide(_ask("vote", "asha", full_set), m, ch, day_after).choice == "ask"
