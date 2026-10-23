from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.policy import ACTIONS, CircleState, PolicyDenied, PolicyEngine, PolicyTicket

NET = Decimal("192.53")  # one net contribution at 3.49% + 0.49 on 200.00
T0 = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


def denied(charter, state: CircleState, action: str, **kwargs) -> str:
    with pytest.raises(PolicyDenied) as ei:
        PolicyEngine(charter, state).authorize(action, **kwargs)
    assert str(ei.value) == ei.value.reason
    return ei.value.reason


def allowed(charter, state: CircleState, action: str, **kwargs) -> PolicyTicket:
    return PolicyEngine(charter, state).authorize(action, **kwargs)


class TestTicketsAndGlobals:
    def test_ticket_shape(self, charter, make_state, completed):
        state = make_state(contributions={**completed, "ravi": "PENDING"}, attempts={})
        t = allowed(charter, state, "charge", member="ravi")
        assert len(t.id) == 32
        int(t.id, 16)
        assert t.issued_at.utcoffset() == timedelta(0)
        assert (t.action, t.circle_id, t.round_n, t.member, t.amount) == (
            "charge",
            "abc123",
            3,
            "ravi",
            Decimal("200.00"),
        )

    def test_ticket_ids_are_unique(self, charter, make_state, completed):
        state = make_state(contributions={**completed, "ravi": "PENDING"}, attempts={})
        ids = {allowed(charter, state, "charge", member="ravi").id for _ in range(5)}
        assert len(ids) == 5

    def test_policy_denied_carries_reason(self):
        e = PolicyDenied("payout amount 1250.00 exceeds circle available 1190.40")
        assert e.reason == str(e) == "payout amount 1250.00 exceeds circle available 1190.40"

    @pytest.mark.parametrize("action", ACTIONS)
    def test_frozen_denies_every_action(self, charter, make_state, action):
        state = make_state(frozen=True, live_payout_exists=True)
        reason = denied(
            charter,
            state,
            action,
            member="ravi",
            amount="200.00",
            swap_with="dev",
            consents={"ravi", "dev", "organizer"},
        )
        assert "frozen" in reason

    def test_charter_hash_mismatch_denied(self, charter, make_state):
        reason = denied(charter, make_state(charter_sha256="0" * 64), "payout", amount="100.00")
        assert "charter hash mismatch" in reason

    def test_unknown_action_denied(self, charter, make_state):
        assert "unknown action" in denied(charter, make_state(), "mint_money")

    def test_round_out_of_range_denied(self, charter, make_state):
        assert "out of range" in denied(charter, make_state(round_n=7), "payout", amount="100.00")

    def test_derived_limits(self, charter, make_state):
        eng = PolicyEngine(charter, make_state())
        assert eng.gross_pot_cap == Decimal("1200.00")
        assert eng.one_net_contribution == NET
        assert eng.expected_recipient() == "dev"


class TestCharge:
    @pytest.fixture
    def pending(self, make_state, completed) -> CircleState:
        return make_state(contributions={**completed, "ravi": "PENDING"}, attempts={})

    def test_charge_allowed_with_charter_amount(self, charter, pending):
        for amount in (None, "200.00", Decimal("200")):
            t = allowed(charter, pending, "charge", member="ravi", amount=amount)
            assert t.amount == Decimal("200.00")

    def test_charge_wrong_amount_denied(self, charter, pending):
        reason = denied(charter, pending, "charge", member="ravi", amount="150.00")
        assert reason == "charge amount 150.00 differs from the charter amount 200.00"
        assert "positive" in denied(charter, pending, "charge", member="ravi", amount="0.00")

    def test_one_success_per_member_per_round(self, charter, make_state):
        assert "already has a completed charge" in denied(charter, make_state(), "charge", member="asha")

    def test_covered_share_is_not_charged(self, charter, make_state, completed):
        state = make_state(contributions={**completed, "ravi": "COVERED"})
        assert "already covered" in denied(charter, state, "charge", member="ravi")

    def test_member_required_and_known(self, charter, pending):
        assert "member is required" in denied(charter, pending, "charge")
        assert "unknown member 'zed'" in denied(charter, pending, "charge", member="zed")

    def test_retries_allowed_up_to_two(self, charter, make_state, completed):
        contributions = {**completed, "ravi": "DECLINED"}
        for prior in (1, 2):
            state = make_state(contributions=contributions, attempts={"ravi": prior})
            assert allowed(charter, state, "retry_charge", member="ravi").member == "ravi"
        state = make_state(contributions=contributions, attempts={"ravi": 3})
        reason = denied(charter, state, "retry_charge", member="ravi")
        assert reason == "ravi has used 2 of 2 retries for round 3"
        assert "retries" in denied(
            charter, state, "charge", member="ravi"
        )  # the limit holds whatever the name

    def test_retry_needs_a_decline(self, charter, pending, make_state, completed):
        assert "nothing to retry" in denied(charter, pending, "retry_charge", member="ravi")
        state = make_state(contributions={**completed, "ravi": "PENDING"}, attempts={"ravi": 1})
        assert "nothing to retry" in denied(charter, state, "retry_charge", member="ravi")

    def test_retry_spacing_of_24_virtual_hours(self, charter, make_state, completed):
        base = {
            "contributions": {**completed, "ravi": "DECLINED"},
            "attempts": {"ravi": 1},
            "last_attempt_at": {"ravi": T0},
        }
        soon = make_state(**base, now=T0 + timedelta(hours=3))
        reason = denied(charter, soon, "retry_charge", member="ravi")
        assert "too soon" in reason and "24h" in reason
        later = make_state(**base, now=T0 + timedelta(hours=25))
        assert allowed(charter, later, "retry_charge", member="ravi").action == "retry_charge"
        unknown_clock = make_state(**base)  # no virtual clock: the engine cannot judge spacing, so it passes
        assert allowed(charter, unknown_clock, "retry_charge", member="ravi").member == "ravi"

    def test_never_retry_payer_action_required(self, charter, make_state, completed):
        state = make_state(
            contributions={**completed, "ravi": "DECLINED"},
            attempts={"ravi": 1},
            decline_codes={"ravi": "PAYER_ACTION_REQUIRED"},
        )
        assert "PAYER_ACTION_REQUIRED" in denied(charter, state, "retry_charge", member="ravi")
        state = make_state(
            contributions={**completed, "ravi": "DECLINED"},
            attempts={"ravi": 1},
            decline_codes={"ravi": "INSTRUMENT_DECLINED"},
        )
        assert allowed(charter, state, "retry_charge", member="ravi").member == "ravi"

    def test_revoked_consent_blocks_charges(self, charter, make_state, completed):
        state = make_state(contributions={**completed, "ravi": "PENDING"}, attempts={}, revoked={"ravi"})
        assert "revoked consent" in denied(charter, state, "charge", member="ravi")


class TestPayout:
    POT = "1154.93"  # 6 x 192.53 - 0.25 reserve

    def test_payout_allowed(self, charter, make_state):
        t = allowed(charter, make_state(), "payout", recipient="dev", amount=self.POT)
        assert (t.member, t.amount) == ("dev", Decimal(self.POT))
        assert allowed(charter, make_state(), "payout", amount=self.POT).member == "dev"  # recipient defaults

    def test_wrong_recipient_denied(self, charter, make_state):
        reason = denied(charter, make_state(), "payout", recipient="ravi", amount=self.POT)
        assert reason == "payout recipient ravi is not round 3's recipient dev"

    def test_approved_swap_changes_the_recipient(self, charter, make_state):
        swapped = make_state(swaps_done={"dev", "ravi"})
        assert allowed(charter, swapped, "payout", recipient="ravi", amount=self.POT).member == "ravi"
        half = make_state(swaps_done={"ravi"})
        assert "not round 3's recipient" in denied(charter, half, "payout", recipient="ravi", amount=self.POT)

    def test_rotation_override_is_the_source_of_truth(self, charter, make_state):
        state = make_state(
            rotation_override=["meena", "asha", "ravi", "lin", "tomas", "dev"], swaps_done={"dev", "ravi"}
        )
        assert allowed(charter, state, "payout", recipient="ravi", amount=self.POT).member == "ravi"
        assert "not round 3's recipient ravi" in denied(
            charter, state, "payout", recipient="dev", amount=self.POT
        )

    def test_amount_over_circle_available_denied(self, charter, make_state):
        state = make_state(circle_available="1190.40")
        reason = denied(charter, state, "payout", amount="1250.00")
        assert reason == "payout amount 1250.00 exceeds circle available 1190.40"
        assert allowed(charter, state, "payout", amount="1190.40").amount == Decimal("1190.40")

    def test_amount_over_gross_pot_cap_denied(self, charter, make_state):
        state = make_state(circle_available="5000.00")
        reason = denied(charter, state, "payout", amount="1200.01")
        assert reason == "payout amount 1200.01 exceeds the gross pot cap 1200.00"
        assert allowed(charter, state, "payout", amount="1200.00").amount == Decimal("1200.00")

    def test_paypal_balance_checked_when_known(self, charter, make_state):
        reason = denied(charter, make_state(paypal_available="900.00"), "payout", amount=self.POT)
        assert reason == "payout amount 1154.93 exceeds the PayPal available balance 900.00"
        assert allowed(charter, make_state(paypal_available=None), "payout", amount=self.POT).member == "dev"

    def test_exactly_one_live_payout(self, charter, make_state):
        reason = denied(charter, make_state(live_payout_exists=True), "payout", amount=self.POT)
        assert "live payout already exists" in reason

    def test_round_must_be_fully_collected(self, charter, make_state, completed):
        declined = make_state(contributions={**completed, "ravi": "DECLINED"})
        reason = denied(charter, declined, "payout", amount=self.POT)
        assert reason == "round 3 is not fully collected: ravi is DECLINED"
        missing = make_state(contributions={h: s for h, s in completed.items() if h != "lin"})
        assert "lin is PENDING" in denied(charter, missing, "payout", amount=self.POT)
        covered = make_state(contributions={**completed, "ravi": "COVERED"})
        assert allowed(charter, covered, "payout", amount=self.POT).member == "dev"

    def test_mandate_or_organizer_approval_required(self, charter, make_state):
        state = make_state(mandate_ok=False)
        assert "mandate" in denied(charter, state, "payout", amount=self.POT)
        assert allowed(charter, state, "payout", amount=self.POT, consents={"organizer"}).member == "dev"

    def test_amount_required_and_positive(self, charter, make_state):
        assert "amount is required" in denied(charter, make_state(), "payout")
        assert "must be positive" in denied(charter, make_state(), "payout", amount="-5.00")
        assert "not a valid money value" in denied(charter, make_state(), "payout", amount="lots")


class TestBufferAdvance:
    @pytest.fixture
    def declined(self, make_state, completed) -> CircleState:
        return make_state(contributions={**completed, "ravi": "DECLINED"}, attempts={"ravi": 1})

    def test_advance_allowed(self, charter, declined):
        t = allowed(charter, declined, "buffer_advance", member="ravi", amount=NET)
        assert (t.member, t.amount) == ("ravi", NET)

    def test_advance_over_buffer_balance_denied(self, charter, make_state, completed):
        state = make_state(contributions={**completed, "ravi": "DECLINED"}, buffer_balance="100.00")
        reason = denied(charter, state, "buffer_advance", member="ravi", amount=NET)
        assert reason == "buffer advance 192.53 exceeds the buffer balance 100.00"

    def test_advance_over_one_net_contribution_denied(self, charter, declined):
        reason = denied(charter, declined, "buffer_advance", member="ravi", amount="192.54")
        assert reason == "buffer advance 192.54 exceeds one net contribution 192.53"

    def test_one_open_advance_per_member(self, charter, make_state, completed):
        state = make_state(contributions={**completed, "ravi": "DECLINED"}, open_advances={"ravi": "150.00"})
        reason = denied(charter, state, "buffer_advance", member="ravi", amount="10.00")
        assert "already has an open advance of 150.00" in reason

    def test_no_advance_for_a_paid_or_covered_share(self, charter, make_state, completed):
        assert "nothing to cover" in denied(
            charter, make_state(), "buffer_advance", member="asha", amount="10.00"
        )
        covered = make_state(contributions={**completed, "ravi": "COVERED"})
        assert "already covered" in denied(charter, covered, "buffer_advance", member="ravi", amount="10.00")

    def test_advance_must_be_in_remedy_menu(self, make_charter, make_state, completed):
        c = make_charter(remedy_menu=["retry_on_date", "turn_swap"])
        state = make_state(for_charter=c, contributions={**completed, "ravi": "DECLINED"})
        reason = denied(c, state, "buffer_advance", member="ravi", amount="10.00")
        assert reason == "buffer_advance is not in the charter's remedy menu (retry_on_date, turn_swap)"

    def test_advance_amount_required(self, charter, declined):
        assert "amount is required" in denied(charter, declined, "buffer_advance", member="ravi")


class TestTurnSwap:
    BOTH = frozenset({"ravi", "dev"})

    def test_swap_allowed_with_both_consents(self, charter, make_state):
        t = allowed(charter, make_state(), "turn_swap", member="ravi", swap_with="dev", consents=self.BOTH)
        assert (t.member, t.amount, t.action) == ("ravi", None, "turn_swap")

    def test_swap_needs_both_consents(self, charter, make_state):
        reason = denied(charter, make_state(), "turn_swap", member="ravi", swap_with="dev", consents={"ravi"})
        assert reason == "turn swap needs consent from both ravi and dev; missing: dev"
        assert "missing: ravi, dev" in denied(
            charter, make_state(), "turn_swap", member="ravi", swap_with="dev"
        )

    def test_at_most_one_swap_per_member(self, charter, make_state):
        state = make_state(swaps_done={"dev"})
        reason = denied(charter, state, "turn_swap", member="ravi", swap_with="dev", consents=self.BOTH)
        assert "dev already swapped once" in reason

    def test_swap_must_be_in_remedy_menu(self, make_charter, make_state):
        c = make_charter(remedy_menu=["buffer_advance"])
        state = make_state(for_charter=c)
        reason = denied(c, state, "turn_swap", member="ravi", swap_with="dev", consents=self.BOTH)
        assert "turn_swap is not in the charter's remedy menu" in reason

    def test_paid_turns_cannot_be_swapped(self, charter, make_state):
        reason = denied(
            charter, make_state(), "turn_swap", member="meena", swap_with="ravi", consents={"meena", "ravi"}
        )
        assert reason == "meena's turn (round 1) is already paid and cannot be swapped"

    def test_turn_with_a_live_payout_cannot_be_swapped(self, charter, make_state):
        state = make_state(live_payout_exists=True)  # round 3 pays dev and the payout is in flight
        reason = denied(charter, state, "turn_swap", member="dev", swap_with="ravi", consents=self.BOTH)
        assert "live payout" in reason and "dev" in reason

    def test_swap_must_keep_the_collateral_rule(self, charter, make_state):
        state = make_state(round_n=1)  # nothing paid yet, so the only obstacle is collateral
        reason = denied(
            charter, state, "turn_swap", member="asha", swap_with="ravi", consents={"asha", "ravi"}
        )
        assert reason.startswith("swap would put ravi (no track record) before asha")
        t = allowed(charter, state, "turn_swap", member="asha", swap_with="meena", consents={"asha", "meena"})
        assert t.member == "asha"

    def test_swap_partners_must_be_distinct_and_known(self, charter, make_state):
        assert "themselves" in denied(charter, make_state(), "turn_swap", member="ravi", swap_with="ravi")
        assert "swap_with is required" in denied(charter, make_state(), "turn_swap", member="ravi")
        assert "unknown swap_with 'zed'" in denied(
            charter, make_state(), "turn_swap", member="ravi", swap_with="zed"
        )


class TestRefundAndCancel:
    def test_refund_allowed_with_organizer_approval(self, charter, make_state):
        t = allowed(charter, make_state(), "refund", member="asha", amount="200.00", consents={"organizer"})
        assert (t.member, t.amount) == ("asha", Decimal("200.00"))

    def test_refund_needs_organizer(self, charter, make_state):
        assert "organizer" in denied(charter, make_state(), "refund", member="asha", amount="200.00")

    def test_refund_at_most_one_contribution(self, charter, make_state):
        reason = denied(
            charter, make_state(), "refund", member="asha", amount="200.01", consents={"organizer"}
        )
        assert reason == "refund 200.01 exceeds one contribution 200.00"

    def test_refund_only_for_a_completed_contribution(self, charter, make_state, completed):
        state = make_state(contributions={**completed, "ravi": "DECLINED"})
        reason = denied(charter, state, "refund", member="ravi", amount="200.00", consents={"organizer"})
        assert "no completed contribution to refund for ravi" in reason

    def test_cancel_unclaimed_needs_a_live_payout(self, charter, make_state):
        assert "no live payout to cancel" in denied(charter, make_state(), "cancel_unclaimed")
        t = allowed(charter, make_state(live_payout_exists=True), "cancel_unclaimed")
        assert (t.member, t.amount) == ("dev", None)
