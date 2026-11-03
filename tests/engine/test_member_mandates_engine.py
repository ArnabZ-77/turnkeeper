"""Member mandates in the engine: the new table, seeding at clone, the policy gate through state.circle_state,
the member agents running over a real voting set. PayPal, the toolkit and the LLM are faked."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from recovery_fakes import FakeInvoiceToolkit, use_swappable_rotation
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.domain import member_mandate as mm
from app.domain.policy import PolicyDenied, PolicyEngine
from app.engine import inbox, member_agent, member_mandates, recovery, rounds, state
from app.engine.member_agent import AgentRequest, Decision
from app.models import Circle, MemberMandateRow, Vote

SIX = ["meena", "asha", "dev", "lin", "ravi", "tomas"]


@pytest.fixture
def toolkit(monkeypatch) -> FakeInvoiceToolkit:
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    return tk


def _rows(circle_id: str) -> list[MemberMandateRow]:
    with session_scope() as db:
        return list(db.exec(select(MemberMandateRow).where(MemberMandateRow.circle_id == circle_id)).all())


def _policy(circle_id: str, round_n: int = 3) -> PolicyEngine:
    return PolicyEngine(state.load_charter(circle_id), state.circle_state(circle_id, round_n))


def _replace(circle_id: str, member: str, mandate: mm.MemberMandate) -> None:
    with session_scope() as db:
        db.add(MemberMandateRow(circle_id=circle_id, member=member, json=mandate.model_dump_json()))


# --- flag off: nothing changes ------------------------------------------------------------------------------


def test_flag_off_by_default_no_rows_no_requirement(circle_id):
    circle = state.load_circle(circle_id)
    assert state.member_mandates_enabled(circle) is False
    assert member_mandates.enabled(circle_id) is False
    assert _rows(circle_id) == []
    assert state.circle_state(circle_id, 3).member_mandate_ok is None
    assert "member_mandates" not in state.get_flags(circle)


def test_flag_off_policy_charges_as_before(circle_id):
    charter = state.load_charter(circle_id)
    ticket = _policy(circle_id).authorize("charge", member="asha", amount=charter.amount)
    assert ticket.member == "asha"


def test_clone_does_not_seed_when_the_global_flag_is_off(circle_id):
    assert member_mandates.wanted_at_clone() is False
    assert _rows(circle_id) == []


def test_kill_switch_beats_the_global_flag(monkeypatch, circle_id):
    monkeypatch.setenv("TK_MEMBER_MANDATES", "1")
    monkeypatch.setenv("TK_MEMBER_AGENTS", "0")
    get_settings.cache_clear()
    assert member_mandates.killed() is True
    assert state.member_mandates_enabled(state.load_circle(circle_id)) is False
    assert member_mandates.wanted_at_clone() is False


# --- seeding and flags --------------------------------------------------------------------------------------


def test_env_flag_seeds_six_signed_mandates_on_clone(monkeypatch):
    from app.engine.sessions import create_session

    monkeypatch.setenv("TK_MEMBER_MANDATES", "1")
    get_settings.cache_clear()
    cid = create_session(enforce_cap=False).circle_id
    assert sorted(r.member for r in _rows(cid)) == sorted(SIX)
    assert state.circle_state(cid, 3).member_mandate_ok is not None
    assert state.get_flags(state.load_circle(cid))["member_mandates"] is True


def test_circle_flag_requires_then_enable_seeds(circle_id):
    out = member_mandates.enable(circle_id)
    assert out == {"seeded": 6}
    assert member_mandates.enabled(circle_id) is True
    assert len(_rows(circle_id)) == 6
    ok = state.circle_state(circle_id, 3).member_mandate_ok
    assert set(ok) == set(SIX) and all(e["ok"] and e["max_charge_per_round"] == "200.00" for e in ok.values())


def test_ensure_seeded_is_idempotent(circle_id):
    member_mandates.enable(circle_id)
    assert member_mandates.ensure_seeded(circle_id) == 0
    assert len(_rows(circle_id)) == 6


def test_enable_keeps_the_existing_flags(circle_id):
    state.set_flag(circle_id, "ravi_declines", False)
    member_mandates.enable(circle_id)
    flags = state.get_flags(state.load_circle(circle_id))
    assert flags["ravi_declines"] is False and flags["member_mandates"] is True
    state.set_flag(circle_id, "next_payout_unclaimed", True)
    assert state.get_flags(state.load_circle(circle_id))["member_mandates"] is True


def test_turning_the_flag_off_restores_none(circle_id):
    member_mandates.enable(circle_id)
    state.set_flag(circle_id, state.MEMBER_MANDATES_FLAG, False)
    assert state.circle_state(circle_id, 3).member_mandate_ok is None
    assert "member_mandates" not in state.get_flags(state.load_circle(circle_id))


def test_enable_refuses_when_killed(monkeypatch, circle_id):
    monkeypatch.setenv("TK_MEMBER_AGENTS", "0")
    get_settings.cache_clear()
    with pytest.raises(PermissionError):
        member_mandates.enable(circle_id)


def test_seeded_mandates_carry_the_circle_charter_and_verify(circle_id):
    member_mandates.enable(circle_id)
    circle = state.load_circle(circle_id)
    m = member_mandates.load_mandate(circle_id, "tomas")
    assert m.charter_sha256 == circle.charter_sha256 and m.circle_id == circle_id
    assert m.rule_summary == mm.TOMAS_RULE and m.signature
    view = member_mandates.mandates_view(circle_id)
    assert all(row["signature_verified"] for row in view["members"])


def test_mandates_view_has_the_honest_labels(circle_id):
    member_mandates.enable(circle_id)
    view = member_mandates.mandates_view(circle_id)
    assert view["label"] == mm.MANDATE_LABEL and view["signature_label"] == mm.SIGNATURE_LABEL
    assert view["enabled"] is True and len(view["members"]) == 6
    tomas = next(r for r in view["members"] if r["member"] == "tomas")
    assert tomas["rules"]["summary"] == "accepts earlier turns, asks for later ones"
    assert tomas["signature"].endswith("…") and len(tomas["signature"]) < 20  # never the whole signature


# --- the policy gate ------------------------------------------------------------------------------------------


def test_policy_allows_a_charge_within_the_mandate(circle_id):
    member_mandates.enable(circle_id)
    charter = state.load_charter(circle_id)
    assert _policy(circle_id).authorize("charge", member="asha", amount=charter.amount).action == "charge"


def test_policy_denies_an_overcharge_before_paypal(circle_id, paypal):
    member_mandates.enable(circle_id)
    with pytest.raises(PolicyDenied) as e:
        _policy(circle_id).authorize("charge", member="ravi", amount="230.00")
    assert e.value.reason == "charge 230.00 exceeds ravi's mandate limit 200.00 per round"
    assert paypal.charges == []


def test_policy_denies_when_a_member_has_no_mandate(circle_id):
    member_mandates.enable(circle_id)
    with session_scope() as db:
        for r in db.exec(select(MemberMandateRow).where(MemberMandateRow.member == "dev")).all():
            db.delete(r)
    with pytest.raises(PolicyDenied, match="no member mandate on file for dev"):
        _policy(circle_id).authorize("charge", member="dev")


def test_policy_denies_a_forged_mandate(circle_id):
    member_mandates.enable(circle_id)
    forged = member_mandates.load_mandate(circle_id, "ravi").model_copy(
        update={"max_charge_per_round": Decimal("500.00")}
    )
    _replace(circle_id, "ravi", forged)  # the newest row wins; its signature no longer verifies
    with pytest.raises(PolicyDenied, match="signature does not verify"):
        _policy(circle_id).authorize("charge", member="ravi")


def test_policy_denies_an_expired_mandate(circle_id):
    member_mandates.enable(circle_id)
    old = member_mandates.load_mandate(circle_id, "ravi").model_copy(update={"expires": date(2026, 1, 1)})
    _replace(circle_id, "ravi", mm.sign_member_mandate(old, get_settings().mandate_hmac_secret))
    with pytest.raises(PolicyDenied, match="expired on 2026-01-01"):
        _policy(circle_id).authorize("charge", member="ravi")


def test_policy_denies_a_mandate_for_an_older_charter(circle_id):
    member_mandates.enable(circle_id)
    stale = member_mandates.load_mandate(circle_id, "ravi").model_copy(update={"charter_sha256": "c" * 64})
    _replace(circle_id, "ravi", mm.sign_member_mandate(stale, get_settings().mandate_hmac_secret))
    with pytest.raises(PolicyDenied, match="not the current charter"):
        _policy(circle_id).authorize("charge", member="ravi")


def test_a_lower_limit_blocks_the_charter_amount(circle_id):
    member_mandates.enable(circle_id)
    low = member_mandates.load_mandate(circle_id, "lin").model_copy(
        update={"max_charge_per_round": Decimal("150.00")}
    )
    _replace(circle_id, "lin", mm.sign_member_mandate(low, get_settings().mandate_hmac_secret))
    with pytest.raises(PolicyDenied, match="limit 150.00"):
        _policy(circle_id).authorize("charge", member="lin")


def test_unreadable_row_means_no_valid_mandate(circle_id):
    member_mandates.enable(circle_id)
    with session_scope() as db:
        db.add(MemberMandateRow(circle_id=circle_id, member="asha", json="{not json"))
    assert member_mandates.load_mandate(circle_id, "asha") is None
    with pytest.raises(PolicyDenied, match="no member mandate on file for asha"):
        _policy(circle_id).authorize("charge", member="asha")


async def test_collect_round_charges_through_the_mandate_gate(vaulted_circle, paypal, collector):
    member_mandates.enable(vaulted_circle)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    assert len(paypal.charges) == 6  # every member's mandate allowed 200.00


async def test_collect_round_is_blocked_before_paypal_without_a_mandate(vaulted_circle, paypal, collector):
    member_mandates.enable(vaulted_circle)
    with session_scope() as db:
        for r in db.exec(select(MemberMandateRow)).all():
            db.delete(r)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    assert paypal.charges == []  # the policy gate refused every charge before any PayPal call


# --- member agents over a real voting set ---------------------------------------------------------------------


@pytest.fixture
async def voting(vaulted_circle, paypal, collector, toolkit):
    """Round 3 with ravi declined, a rules proposal and an open vote; mandates ON (ravi round 5, tomas round 6)."""
    cid = vaulted_circle
    use_swappable_rotation(cid)
    member_mandates.enable(cid)
    await rounds.collect_round(cid, 3, publish=collector)
    inbox.add_message(cid, "ravi", inbox.DEFAULT_RAVI_REPLY, lang="hi", direction="in", source="judge")
    out = await recovery.run_rules_recovery(cid, publish=collector)
    assert out["vote"]["status"] == "voting"
    return cid, out["set_id"]


def _votes(set_id: str) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    with session_scope() as db:
        return {
            (v.kind, v.member): (v.choice, v.source)
            for v in db.exec(select(Vote).where(Vote.set_id == set_id))
        }


async def test_agents_answer_consents_and_votes(voting, collector):
    cid, sid = voting
    res = await member_agent.run_member_agents(cid, sid, publish=collector)
    by = {(d["kind"], d["member"]): d for d in res["decisions"]}
    assert by[("consent", "tomas")]["choice"] == "yes" and by[("consent", "tomas")]["rule"] == "swap_earlier"
    assert by[("consent", "ravi")]["choice"] == "ask" and by[("consent", "ravi")]["rule"] == "swap_later_ask"
    assert res["labels"]["mandate"] == mm.MANDATE_LABEL and res["labels"]["signature"] == mm.SIGNATURE_LABEL


async def test_agent_answers_are_recorded_with_source_agent(voting, collector):
    cid, sid = voting
    await member_agent.run_member_agents(cid, sid, publish=collector)
    rec = _votes(sid)
    assert rec[("consent", "tomas")] == ("yes", "agent")
    assert rec[("vote", "asha")] == ("yes", "agent")
    assert all(src == "agent" for (_k, _m), (choice, src) in rec.items() if choice in ("yes", "no"))


async def test_ask_leaves_the_item_pending_and_posts_to_the_inbox(voting, collector):
    cid, sid = voting
    res = await member_agent.run_member_agents(cid, sid, publish=collector)
    assert ("consent", "ravi") in _votes(sid) and _votes(sid)[("consent", "ravi")][0] in (None, "pending")
    assert "ravi" in res["asked"] and "ravi" in res["pending"]
    msgs = [m for m in inbox.list_messages(cid, member="ravi", direction="out") if m["source"] == "agent"]
    assert len(msgs) == 1 and msgs[0]["lang"] == "hi"  # ravi is asked in his own language
    assert res["consents"]["given"] == ["tomas"]


async def test_running_twice_does_not_repost_the_same_question(voting, collector):
    cid, sid = voting
    await member_agent.run_member_agents(cid, sid, publish=collector)
    n1 = len([m for m in inbox.list_messages(cid, member="ravi", direction="out") if m["source"] == "agent"])
    again = await member_agent.run_member_agents(cid, sid, publish=collector)
    n2 = len([m for m in inbox.list_messages(cid, member="ravi", direction="out") if m["source"] == "agent"])
    assert n1 == n2 == 1
    assert all(
        d["member"] != "tomas" or d["kind"] != "consent" for d in again["decisions"]
    )  # already answered


async def test_the_humans_tap_answers_an_ask(voting, collector):
    cid, sid = voting
    res = await member_agent.run_member_agents(
        cid, sid, answers={"ravi": "yes"}, answer_source="member", publish=collector
    )
    assert _votes(sid)[("consent", "ravi")] == ("yes", "member")
    assert sorted(res["consents"]["given"]) == ["ravi", "tomas"]


async def test_a_tap_is_ignored_where_the_agent_decides(voting, collector):
    cid, sid = voting
    await member_agent.run_member_agents(cid, sid, answers={"tomas": "no", "asha": "no"}, publish=collector)
    assert _votes(sid)[("consent", "tomas")] == ("yes", "agent")
    assert _votes(sid)[("vote", "asha")] == ("yes", "agent")


async def test_invalid_taps_are_dropped(voting, collector):
    cid, sid = voting
    await member_agent.run_member_agents(cid, sid, answers={"ravi": "maybe"}, publish=collector)
    assert _votes(sid)[("consent", "ravi")][0] in (None, "pending")


async def test_votes_pass_quorum_with_agents_alone(voting, collector):
    cid, sid = voting
    res = await member_agent.run_member_agents(cid, sid, kinds=("vote",), publish=collector)
    assert res["votes"]["passed"] is True and res["votes"]["yes"] >= 4


async def test_a_tight_advance_cap_makes_that_agent_vote_no(voting, collector):
    cid, sid = voting
    low = member_mandates.load_mandate(cid, "dev").model_copy(update={"max_advance": Decimal("10.00")})
    _replace(cid, "dev", mm.sign_member_mandate(low, get_settings().mandate_hmac_secret))
    res = await member_agent.run_member_agents(cid, sid, kinds=("vote",), publish=collector)
    dev = next(d for d in res["decisions"] if d["member"] == "dev")
    assert (dev["choice"], dev["rule"]) == ("no", "advance_over_cap")
    assert _votes(sid)[("vote", "dev")] == ("no", "agent")


async def test_a_member_without_a_mandate_is_asked_not_assumed(voting, collector):
    cid, sid = voting
    with session_scope() as db:
        for r in db.exec(select(MemberMandateRow).where(MemberMandateRow.member == "lin")).all():
            db.delete(r)
    res = await member_agent.run_member_agents(cid, sid, kinds=("vote",), publish=collector)
    lin = next(d for d in res["decisions"] if d["member"] == "lin")
    assert (lin["choice"], lin["rule"]) == ("ask", "no_mandate") and "lin" in res["asked"]


async def test_a_forged_mandate_makes_the_agent_ask(voting, collector):
    cid, sid = voting
    forged = member_mandates.load_mandate(cid, "lin").model_copy(update={"max_advance": Decimal("999.00")})
    _replace(cid, "lin", forged)
    res = await member_agent.run_member_agents(cid, sid, kinds=("vote",), publish=collector)
    lin = next(d for d in res["decisions"] if d["member"] == "lin")
    assert lin["rule"] == "no_mandate" and "does not verify" in lin["reason"]


async def test_run_refuses_when_the_flag_is_off(vaulted_circle, collector):
    with pytest.raises(RuntimeError):
        await member_agent.run_member_agents(vaulted_circle, publish=collector)


async def test_run_needs_an_open_vote(vaulted_circle, collector):
    member_mandates.enable(vaulted_circle)
    with pytest.raises(recovery.RecoveryError):
        await member_agent.run_member_agents(vaulted_circle, publish=collector)


async def test_agents_publish_trace_lines(voting, collector):
    cid, sid = voting
    await member_agent.run_member_agents(cid, sid, publish=collector)
    texts = [e["text"] for e in collector.events if e.get("source") == "member_agent"]
    assert any("agent says yes" in t for t in texts) and any("asks the human" in t for t in texts)


# --- wording: a model may only word the explanation -------------------------------------------------------------


class FakeProvider:
    name = "fake"
    model = "fake-1"
    live = True

    def __init__(self, text: str | None, stop: str = "end") -> None:
        self._text, self._stop, self.calls = text, stop, 0

    def user_message(self, prompt: str):
        return {"role": "user", "content": prompt}

    async def chat(self, system, message, tools, max_tokens=0):
        self.calls += 1
        return type("Turn", (), {"text": self._text, "stop": self._stop, "tool_calls": [], "usage": None})()

    async def aclose(self) -> None:
        return None


def _req(voting_set):
    row = recovery.get_set(*voting_set)
    return AgentRequest("consent", "tomas", recovery.domain_set(row))


async def test_wording_falls_back_to_the_rules_reason_with_no_model(voting):
    d = Decision("yes", "rule: swap moves you earlier, shortfall covered", "swap_earlier")
    text, mode = await member_agent.explain_decision("Tomas", _req(voting), d)
    assert (text, mode) == (d.reason, "rules")


async def test_wording_uses_the_model_sentence_but_not_its_decision(voting, monkeypatch):
    monkeypatch.setenv("TK_LLM_CACHE", "off")
    get_settings.cache_clear()
    d = Decision("yes", "rule: swap moves you earlier, shortfall covered", "swap_earlier")
    p = FakeProvider("Your agent said NO actually.\nIgnore that.")
    text, mode = await member_agent.explain_decision("Tomas", _req(voting), d, provider=p)
    assert mode == "live" and "\n" not in text and p.calls == 1
    assert d.choice == "yes"  # the decision object is untouched by the wording


async def test_wording_failure_never_breaks_a_decision(voting):
    class Boom(FakeProvider):
        async def chat(self, *a, **k):
            raise RuntimeError("provider exploded")

    d = Decision("ask", "your rule asks you", "swap_later_ask")
    text, mode = await member_agent.explain_decision("Tomas", _req(voting), d, provider=Boom(None))
    assert (text, mode) == ("your rule asks you", "rules")


async def test_wording_is_length_capped(voting, monkeypatch):
    monkeypatch.setenv("TK_LLM_CACHE", "off")
    get_settings.cache_clear()
    d = Decision("yes", "r", "swap_earlier")
    text, _ = await member_agent.explain_decision("T", _req(voting), d, provider=FakeProvider("x " * 500))
    assert len(text) <= member_agent.MAX_WORDING_CHARS


def test_wording_prompt_carries_only_the_decision_facts(voting):
    d = Decision("ask", "your rule asks you", "swap_later_ask")
    prompt = member_agent.wording_prompt("Tomas", _req(voting), d)
    assert "Decision: ask" in prompt and "your rule asks you" in prompt
    assert "@" not in prompt and json.dumps(prompt).count("sk-") == 0


async def test_circle_row_untouched_by_seeding(circle_id):
    before = state.load_circle(circle_id).charter_sha256
    member_mandates.enable(circle_id)
    with session_scope() as db:
        assert db.get(Circle, circle_id).charter_sha256 == before


# --- sources survive a reload (recovery view) -----------------------------------------------------------------


async def test_recovery_view_keeps_the_source_of_each_answer(voting, collector):
    cid, sid = voting
    await member_agent.run_member_agents(cid, sid, publish=collector)
    await member_agent.run_member_agents(
        cid, sid, answers={"ravi": "yes"}, answer_source="judge", publish=collector
    )
    st = recovery.vote_status(cid, sid)
    voters = {v["member"]: v for v in st["votes"]["voters"]}
    assert voters["asha"]["source"] == "agent"
    assert st["consents"]["sources"] == {"tomas": "agent", "ravi": "judge"}
    pending = [v for v in st["votes"]["voters"] if v["choice"] == "pending"]
    assert all("source" not in v for v in pending)


def test_recovery_view_has_no_sources_with_the_flag_off(circle_id):
    # the flag-off shape is the pre-feature one (see tests/routes/test_member_mandates_regression.py)
    assert recovery._sources(circle_id, "rs_none", "vote") == {}
