"""Remedy context assembly: the computed facts match the validator, and the guide states the preference order."""

from datetime import date

import pytest

from app.agent import remedy_context as rc
from app.domain.remedies import Remedy, RemedyParams, RemedySet, effective_rotation, validate_remedy_set
from eval import run_eval as ev


@pytest.fixture(scope="module")
def scenarios():
    return ev.load_scenarios()


def _swap_problems(charter, state, member, partner, refused):
    h = ev.charter_sha256(charter)
    rs = RemedySet(
        member=member,
        round_n=state.round_n,
        charter_sha256=h,
        remedies=[
            Remedy(type="turn_swap", member=member, round_n=state.round_n, charter_sha256=h,
                   params=RemedyParams(swap_with=partner)),
            Remedy(type="retry_on_date", member=member, round_n=state.round_n, charter_sha256=h,
                   params=RemedyParams(due_date=date(2027, 1, 8))),
        ],
    )  # fmt: skip
    consents = {h for h in (member, partner) if h not in refused}
    problems = validate_remedy_set(charter, state, rs, consents=consents)
    return [p for p in problems if not any(w in p for w in ("retry", "retried", "retries"))]


def test_legal_swap_partners_agree_with_the_validator(scenarios):
    """Every listed partner passes the validator's swap checks (with consents); every other member fails them."""
    for sc in scenarios:
        charter = ev.build_charter(sc)
        state = ev.build_state(sc, charter)
        member, refused = sc["decline"]["member"], ev.refused_of(sc)
        partners, why_none = rc.legal_swap_partners(charter, state, member, refused)
        assert bool(partners) != bool(why_none), sc["id"]
        if "turn_swap" not in charter.remedy_menu:
            assert partners == [] and "menu" in why_none
            continue
        for other in effective_rotation(charter, state):
            if other == member:
                continue
            problems = _swap_problems(charter, state, member, other, refused)
            assert (other in partners) == (not problems), (sc["id"], other, problems)


def test_advance_facts_respect_buffer_net_cap_and_open_advance(scenarios):
    by = {s["id"]: s for s in scenarios}
    expect = {
        "s01-payday-friday-hi": (True, "192.53"),  # capped by one net contribution, not the 250.00 buffer
        "s14-small-buffer": (True, "100.00"),  # capped by the buffer
        "s07-empty-buffer": (False, "0.00"),
        "s08-second-advance": (False, "0.00"),  # an advance is already open
    }
    for sid, (allowed, amount) in expect.items():
        charter = ev.build_charter(by[sid])
        state = ev.build_state(by[sid], charter)
        facts = rc.advance_facts(charter, state, by[sid]["decline"]["member"])
        assert (facts["allowed"], facts["max_amount"]) == (allowed, amount), sid
    s08 = rc.advance_facts(
        ev.build_charter(by["s08-second-advance"]),
        ev.build_state(by["s08-second-advance"], ev.build_charter(by["s08-second-advance"])),
        "ravi",
    )
    assert "open advance" in s08["why_not"]


def test_retry_facts_block_payer_action_required(scenarios):
    sc = next(s for s in scenarios if s["id"] == "s17-payer-action-empty-buffer")
    charter = ev.build_charter(sc)
    state = ev.build_state(sc, charter)
    facts = rc.retry_facts(charter, state, "ravi", date(2027, 1, 2))
    assert not facts["allowed"] and "PAYER_ACTION_REQUIRED" in facts["why_not"]
    ok = next(s for s in scenarios if s["id"] == "s01-payday-friday-hi")
    c1 = ev.build_charter(ok)
    f1 = rc.retry_facts(c1, ev.build_state(ok, c1), "ravi", date(2027, 1, 2))
    assert f1["allowed"] and f1["retries_left"] == 2 and f1["earliest_date"] == "2027-01-03"


def test_calendar_maps_hindi_weekdays():
    cal = rc.calendar(date(2027, 1, 2))  # a Saturday
    assert len(cal) == rc.CALENDAR_DAYS and cal[0]["date"] == "2027-01-03"
    friday = next(d for d in cal if d["weekday"] == "Friday")
    assert friday == {"date": "2027-01-08", "weekday": "Friday", "hi": "शुक्रवार", "hi_latin": "shukravar"}


def test_guide_states_the_preference_order():
    guide = rc.remedy_guide()
    for phrase in (
        "buffer_advance.max_amount",
        "Do NOT add `retry_on_date`",
        "turn_swap.legal_partners",
        "empty `remedies` list",
        "calendar",
        "Vague words",
        "<member_reply>",
    ):
        assert phrase in guide
    assert guide.index("Cover the round") < guide.index("Protect the circle")


def test_facts_carry_no_names_or_emails(scenarios):
    sc = scenarios[0]
    charter = ev.build_charter(sc)
    facts = rc.remedy_facts(charter, ev.build_state(sc, charter), "ravi")
    text = str(facts)
    assert "Ravi Sharma" not in text and "@" not in text
    assert facts["turn_swap"]["legal_partners"] == ["tomas"] and facts["default_due_date"] == "2027-01-08"
