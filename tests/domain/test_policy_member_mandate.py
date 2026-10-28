"""PolicyEngine._charge with member mandates: None = not required (old behaviour), a dict = required."""

from __future__ import annotations

import pytest

from app.domain.policy import CircleState, PolicyDenied, PolicyEngine


def _entry(ok=True, limit="200.00", reason="mandate ok"):
    return {"ok": ok, "reason": reason, "max_charge_per_round": limit}


@pytest.fixture
def pending_state(make_state, charter):
    def _make(**over):
        handles = [m.handle for m in charter.members]
        data = {"contributions": {h: "PENDING" for h in handles}, "attempts": {}}
        data.update(over)
        return make_state(**data)

    return _make


def test_default_is_not_required():
    assert CircleState.model_fields["member_mandate_ok"].default is None


def test_none_means_the_old_path(charter, pending_state):
    st = pending_state()
    assert st.member_mandate_ok is None
    ticket = PolicyEngine(charter, st).authorize("charge", member="ravi", amount="200.00")
    assert ticket.action == "charge" and ticket.member == "ravi"


def test_required_and_valid_allows_the_charge(charter, pending_state):
    st = pending_state(member_mandate_ok={"ravi": _entry()})
    assert PolicyEngine(charter, st).authorize("charge", member="ravi").amount == 200


def test_missing_entry_denies(charter, pending_state):
    st = pending_state(member_mandate_ok={"asha": _entry()})
    with pytest.raises(PolicyDenied, match="no member mandate on file for ravi"):
        PolicyEngine(charter, st).authorize("charge", member="ravi")


def test_empty_dict_is_required_and_denies_everyone(charter, pending_state):
    st = pending_state(member_mandate_ok={})
    with pytest.raises(PolicyDenied, match="no member mandate"):
        PolicyEngine(charter, st).authorize("charge", member="asha")


def test_invalid_mandate_denies_with_its_reason(charter, pending_state):
    st = pending_state(
        member_mandate_ok={"ravi": _entry(ok=False, reason="ravi's mandate signature does not verify")}
    )
    with pytest.raises(PolicyDenied, match="signature does not verify.*charge blocked"):
        PolicyEngine(charter, st).authorize("charge", member="ravi")


def test_expired_mandate_denies(charter, pending_state):
    st = pending_state(
        member_mandate_ok={"ravi": _entry(ok=False, reason="ravi's mandate expired on 2026-01-01")}
    )
    with pytest.raises(PolicyDenied, match="expired on 2026-01-01"):
        PolicyEngine(charter, st).authorize("charge", member="ravi")


def test_over_the_limit_is_denied_with_a_readable_reason(charter, pending_state):
    st = pending_state(member_mandate_ok={"ravi": _entry()})
    with pytest.raises(PolicyDenied) as e:
        PolicyEngine(charter, st).authorize("charge", member="ravi", amount="230.00")
    assert e.value.reason == "charge 230.00 exceeds ravi's mandate limit 200.00 per round"


def test_lower_limit_denies_the_charter_amount(charter, pending_state):
    st = pending_state(member_mandate_ok={"ravi": _entry(limit="150.00")})
    with pytest.raises(PolicyDenied, match="exceeds ravi's mandate limit 150.00"):
        PolicyEngine(charter, st).authorize("charge", member="ravi")


def test_entry_without_a_limit_denies(charter, pending_state):
    st = pending_state(member_mandate_ok={"ravi": _entry(limit=None)})
    with pytest.raises(PolicyDenied, match="no charge limit"):
        PolicyEngine(charter, st).authorize("charge", member="ravi")


def test_mandate_check_also_guards_retry_charge(charter, pending_state):
    st = pending_state(
        member_mandate_ok={"ravi": _entry(ok=False, reason="expired")},
        contributions={m.handle: "DECLINED" if m.handle == "ravi" else "PENDING" for m in charter.members},
        attempts={"ravi": 1},
    )
    with pytest.raises(PolicyDenied, match="expired"):
        PolicyEngine(charter, st).authorize("retry_charge", member="ravi")


def test_other_actions_are_untouched_by_the_mandate_requirement(charter, make_state):
    st = make_state(member_mandate_ok={})
    ticket = PolicyEngine(charter, st).authorize("payout", recipient="dev", amount="1154.93")
    assert ticket.action == "payout"


def test_an_unknown_member_is_denied_before_the_mandate_check(charter, pending_state):
    st = pending_state(member_mandate_ok={})
    with pytest.raises(PolicyDenied, match="unknown member"):
        PolicyEngine(charter, st).authorize("charge", member="nobody")
