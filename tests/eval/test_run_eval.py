"""Remedy eval harness: scenarios, the rules path, the gates, the live-mode budget (fake model only)."""

import json
from datetime import date

import pytest

from eval import run_eval as ev


@pytest.fixture(scope="module")
def scenarios():
    return ev.load_scenarios()


def by_id(scenarios, sid):
    return next(s for s in scenarios if s["id"] == sid)


def test_twenty_scenarios_with_valid_charters_and_labels(scenarios):
    assert len(scenarios) == 20
    covered = " ".join(s["id"] for s in scenarios)
    for theme in (
        "payday",
        "no-reply",
        "angry",
        "injection",
        "empty-buffer",
        "second-advance",
        "refuses",
        "hindi",
    ):
        assert theme in covered
    for s in scenarios:
        charter = ev.build_charter(s)  # raises on a collateral-invalid rotation
        ev.build_state(s, charter)
        labels = s["labels"]
        assert {"legal_types", "must_not", "preferred_top", "commitment"} <= set(labels)
        ev.expected_commitment(s, charter)


def test_expected_commitment_rules(scenarios):
    charter = ev.build_charter(by_id(scenarios, "s01-payday-friday-hi"))
    today = date(2027, 1, 2)  # a Saturday
    assert ev.expected_commitment(by_id(scenarios, "s01-payday-friday-hi"), charter)[1] == date(2027, 1, 8)
    assert ev.expected_commitment(by_id(scenarios, "s12-pay-tomorrow"), charter)[1] == date(2027, 1, 3)
    assert ev.expected_commitment(by_id(scenarios, "s13-iso-date"), charter)[1] == date(2027, 1, 12)
    assert ev.expected_commitment(by_id(scenarios, "s18-recipient-is-short"), charter)[1] == date(2027, 1, 4)
    assert ev.expected_commitment(by_id(scenarios, "s03-no-reply"), charter) is None
    assert ev.scenario_today(by_id(scenarios, "s01-payday-friday-hi")) == today


async def test_rules_mode_metrics(scenarios):
    res = await ev.run_eval(provider="rules", runs=2, scenarios=scenarios)
    assert res["runs_completed"] == 40 and res["llm_calls_used"] == 0 and res["modes"] == {"rules": 40}
    assert res["executed_illegal"] == 0
    assert res["charter_legal_before_policy_rate"] == pytest.approx(0.95)
    assert res["preferred_top_agreement"] == pytest.approx(0.95)
    assert res["commitment_exact_match"] == 1.0 and res["top_remedy_stability"] == 1.0
    s17 = next(s for s in res["scenario_results"] if s["id"] == "s17-payer-action-empty-buffer")
    row = s17["rows"][0]
    assert not row["legal_before_policy"] and row["executed"] == []
    # The proposer no longer offers the never-retry charge; the uncovered set still goes to the organizer.
    assert "does not cover" in row["blocked"] and row["must_not_proposed"] == []


async def test_injection_amount_is_ignored_by_rules(scenarios):
    res = await ev.run_eval(provider="rules", runs=1, scenarios=[by_id(scenarios, "s06-injection-amount-hi")])
    row = res["scenario_results"][0]["rows"][0]
    assert row["commitment"] == {"amount": "200.00", "date": "2027-01-08"}
    assert all(r["amount"] in (None, "192.53", "200.00") for r in row["remedies"])


async def test_swap_refusals(scenarios):
    for sid in ("s04-angry-reply", "s09-swap-target-refuses"):
        res = await ev.run_eval(provider="rules", runs=1, scenarios=[by_id(scenarios, sid)])
        assert res["scenario_results"][0]["modal_top"] == "buffer_advance+repay_by_invoice"


def _live(texts):
    calls = []

    async def call(system, prompt, live_allowed=True):
        if not live_allowed:  # budget spent: this fake has no recordings
            return None, "rules"
        calls.append(prompt)
        item = texts[min(len(calls) - 1, len(texts) - 1)]
        return item if isinstance(item, tuple) else (item, "live")

    return call, calls


GOOD = json.dumps(
    {
        "commitment": {"amount": "200.00", "date": "2027-01-08"},
        "remedies": [
            {
                "type": "buffer_advance",
                "amount": "192.53",
                "swap_with": None,
                "due_date": None,
                "reason": "cover",
            },
            {"type": "repay_by_invoice", "amount": "200.00", "due_date": "2027-01-08", "reason": "repay"},
            {"type": "turn_swap", "swap_with": "tomas", "reason": "collateral"},
        ],
    }
)
ILLEGAL = json.dumps(
    {
        "commitment": {"amount": "500.00", "date": "2027-01-08"},
        "remedies": [
            {"type": "buffer_advance", "amount": "500.00"},
            {"type": "turn_swap", "swap_with": "meena"},
        ],
    }
)


async def test_live_mode_scores_model_output(scenarios):
    call, calls = _live([GOOD])
    sc = [by_id(scenarios, "s01-payday-friday-hi")]
    res = await ev.run_eval(
        provider="live", runs=3, max_calls=10, scenarios=sc, live_call=call, model="fake/m"
    )
    assert res["llm_calls_used"] == 3 and res["modes"] == {"live": 3}
    assert res["preferred_top_agreement"] == 1.0 and res["commitment_exact_match"] == 1.0
    assert res["executed_illegal"] == 0
    # each run is a distinct prompt (a distinct replay-cache key); member text is framed as data
    assert len(set(calls)) == 3 and "<member_reply" in calls[0] and "Ravi Sharma" not in calls[0]


async def test_live_illegal_proposal_is_blocked_and_counted(scenarios):
    call, _ = _live([ILLEGAL])
    sc = [by_id(scenarios, "s05-injection-advance")]
    res = await ev.run_eval(provider="live", runs=1, max_calls=5, scenarios=sc, live_call=call)
    row = res["scenario_results"][0]["rows"][0]
    assert not row["legal_before_policy"] and row["executed"] == [] and res["executed_illegal"] == 0
    assert {"advance_over_buffer", "injection_amount", "swap_moves_member_earlier"} <= set(
        row["proposal_violations"]
    )
    assert row["commitment"] is None and "at most" in row["commitment_rejected"]
    assert res["must_not_proposed"] >= 2


async def test_live_budget_stops_gracefully(scenarios):
    call, calls = _live([GOOD])
    res = await ev.run_eval(provider="live", runs=5, max_calls=7, scenarios=scenarios[:3], live_call=call)
    assert len(calls) == 7 and res["llm_calls_used"] == 7 and res["runs_completed"] == 7
    assert res["runs_planned"] == 15 and "max-calls 7" in res["stopped_early"]


async def test_replayed_turns_do_not_spend_budget(scenarios):
    call, calls = _live([(GOOD, "replay")])
    res = await ev.run_eval(provider="live", runs=2, max_calls=1, scenarios=scenarios[:3], live_call=call)
    assert res["runs_completed"] == 6 and res["llm_calls_used"] == 0 and res["modes"] == {"replay": 6}


async def test_unavailable_model_falls_back_to_rules_and_stops(scenarios):
    call, calls = _live([(None, "rules")])
    res = await ev.run_eval(provider="live", runs=5, max_calls=40, scenarios=scenarios[:2], live_call=call)
    assert len(calls) == 3 and res["modes"] == {"rules": 3} and "unavailable" in res["stopped_early"]


async def test_unparseable_output_is_an_illegal_live_proposal(scenarios):
    call, _ = _live(["I think we should be kind to Ravi."])
    res = await ev.run_eval(provider="live", runs=1, max_calls=2, scenarios=scenarios[:1], live_call=call)
    row = res["scenario_results"][0]["rows"][0]
    assert row["mode"] == "live" and row["signature"] == "unparsed" and row["parse_error"]
    assert not row["legal_before_policy"] and row["executed"] == []


def test_gate_requires_consents(scenarios):
    sc = by_id(scenarios, "s09-swap-target-refuses")
    charter = ev.build_charter(sc)
    state = ev.build_state(sc, charter)
    proposal = ev.parse_live(GOOD, sc, charter)  # includes the swap with tomas, who refuses
    executed, blocked = ev.execute_gate(charter, state, proposal.remedy_set, ev.refused_of(sc))
    assert executed == [] and "consent" in blocked


def test_write_outputs_merges_providers(tmp_path, scenarios):
    import asyncio

    rules = asyncio.run(ev.run_eval(provider="rules", runs=1, scenarios=scenarios[:2]))
    call, _ = _live([GOOD])
    live = asyncio.run(
        ev.run_eval(provider="live", runs=1, scenarios=scenarios[:2], live_call=call, model="x/y")
    )
    (tmp_path / "results_forecast.json").write_text(
        json.dumps(__import__("eval.forecast_backtest", fromlist=["run"]).run()), encoding="utf-8"
    )
    ev.write_outputs(rules, tmp_path)
    results_path, md_path = ev.write_outputs(live, tmp_path)
    merged = json.loads(results_path.read_text(encoding="utf-8"))
    assert set(merged) == {"rules", "live"}
    md = md_path.read_text(encoding="utf-8")
    assert "| Metric | rules | live |" in md and "Executed illegal (must be 0)" in md
    assert "## Forecaster backtest" in md


def test_cli_rules_and_live_without_provider(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    get_settings.cache_clear()
    assert ev.main(["--provider", "rules", "--runs", "1", "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "results.json").exists() and (tmp_path / "RESULTS.md").exists()
    assert ev.main(["--provider", "live", "--out-dir", str(tmp_path / "x")]) == 2  # no model: nothing written
    assert not (tmp_path / "x").exists()
    get_settings.cache_clear()


def test_committed_rules_results_are_current():
    data = json.loads((ev.EVAL_DIR / "results.json").read_text(encoding="utf-8"))
    rules = data["rules"]
    assert rules["executed_illegal"] == 0 and rules["runs_completed"] == 100 and rules["llm_calls_used"] == 0
    assert (ev.EVAL_DIR / "RESULTS.md").read_text(encoding="utf-8").startswith("# Eval results")


async def test_spent_budget_still_runs_recorded_answers(scenarios):
    """Fixer: replays cost no quota, so they keep running after --max-calls; only unrecorded runs are skipped."""
    live_calls, replay_calls = [], []

    async def call(system, prompt, live_allowed=True):
        if live_allowed:
            live_calls.append(prompt)
            return GOOD, "live"
        replay_calls.append(prompt)
        return (GOOD, "replay") if len(replay_calls) % 2 else (None, "rules")

    res = await ev.run_eval(provider="live", runs=2, max_calls=2, scenarios=scenarios[:3], live_call=call)
    assert len(live_calls) == 2 and res["llm_calls_used"] == 2 and len(replay_calls) == 4
    assert res["modes"] == {"live": 2, "replay": 2} and res["runs_completed"] == 4
    assert "max-calls 2" in res["stopped_early"]


async def test_unparseable_replay_is_labelled_replay(scenarios):
    call, _ = _live([("I think we should be kind to Ravi.", "replay")])
    res = await ev.run_eval(provider="live", runs=1, max_calls=2, scenarios=scenarios[:1], live_call=call)
    row = res["scenario_results"][0]["rows"][0]
    assert row["mode"] == "replay" and row["signature"] == "unparsed" and row["parse_error"]
    sc = scenarios[0]
    charter = ev.build_charter(sc)
    assert ev.parse_live("no json", sc, charter, mode="replay").mode == "replay"


# --- v2 prompt: the guide + computed facts (offline, fake model only) ----------------------------------------


def _facts_of(prompt):
    start = prompt.index("{")
    end = prompt.index("\nLatest reply:")
    return json.loads(prompt[start:end])


def _follow_guide(facts, commitment_date):
    """A literal reading of app/agent/prompts/remedies.md applied to the facts the prompt carries."""
    menu = facts["remedy_menu"]
    due = commitment_date or facts["default_due_date"]
    remedies = []
    if facts["buffer_advance"]["allowed"]:
        remedies.append({"type": "buffer_advance", "amount": facts["buffer_advance"]["max_amount"]})
        if "repay_by_invoice" in menu:
            remedies.append({"type": "repay_by_invoice", "amount": facts["contribution"], "due_date": due})
    elif facts["retry_on_date"]["allowed"]:
        remedies.append(
            {"type": "retry_on_date", "due_date": max(due, facts["retry_on_date"]["earliest_date"])}
        )
    if remedies and facts["turn_swap"]["legal_partners"]:
        remedies.append({"type": "turn_swap", "swap_with": facts["turn_swap"]["legal_partners"][0]})
    for r in remedies:
        r["reason"] = "guide"
    commitment = {"amount": facts["contribution"], "date": commitment_date} if commitment_date else None
    return json.dumps({"commitment": commitment, "remedies": remedies})


async def test_following_the_guide_matches_every_label(scenarios):
    """The guide plus the computed facts are enough to reach every preferred_top label (incl. s17 = none):
    the labels and the stated preference order agree, and nothing a literal reader proposes is illegal."""
    by_prompt = {}
    for sc in scenarios:
        charter = ev.build_charter(sc)
        exp = ev.expected_commitment(sc, charter)
        prompt = ev.live_prompt(sc, charter, ev.build_state(sc, charter), 1)
        by_prompt[prompt] = exp[1].isoformat() if exp else None
    assert len(by_prompt) == len(scenarios)  # every scenario's prompt is distinct

    async def call(system, prompt, live_allowed=True):
        assert system == ev.live_system("v2")
        return _follow_guide(_facts_of(prompt), by_prompt[prompt]), "live"

    res = await ev.run_eval(provider="live", runs=1, max_calls=40, scenarios=scenarios, live_call=call)
    assert res["preferred_top_agreement"] == 1.0, [
        (s["id"], s["modal_top"], s["preferred_top"])
        for s in res["scenario_results"]
        if s["agreement"] != 1.0
    ]
    assert res["charter_legal_before_policy_rate"] == 1.0 and res["executed_illegal"] == 0
    assert res["commitment_exact_match"] == 1.0 and res["must_not_proposed"] == 0
    assert res["prompt_version"] == "v2"


def test_v1_prompt_is_frozen_for_replay():
    """The 2026-10-24 recordings replay only while the v1 system prompt stays byte-identical."""
    import hashlib

    digest = hashlib.sha256(ev.LIVE_SYSTEM_V1.encode()).hexdigest()
    assert digest == "ce72cfaa1d93c070a3210bf86f679da2d50e6742feeb84fa5e36d2ba3f38d802"
    assert ev.live_system("v1") == ev.LIVE_SYSTEM_V1 and ev.LIVE_SYSTEM == ev.live_system("v2")


def test_v2_prompt_carries_the_computed_facts(scenarios):
    sc = by_id(scenarios, "s20-swap-breaks-collateral")
    charter = ev.build_charter(sc)
    prompt = ev.live_prompt(sc, charter, ev.build_state(sc, charter), 1)
    facts = _facts_of(prompt)
    assert facts["turn_swap"]["legal_partners"] == [] and "collateral" in facts["turn_swap"]["why_none"]
    assert facts["buffer_advance"]["max_amount"] == "192.53" and facts["run"] == 1
    assert "<member_reply" in prompt and "shukravar" in prompt


def test_splits_are_disjoint_and_cover_every_scenario(scenarios):
    tuning = ev.select_split(scenarios, "tuning")
    heldout = ev.select_split(scenarios, "heldout")
    assert len(tuning) == 12 and len(heldout) == 8
    ids_t, ids_h = {s["id"] for s in tuning}, {s["id"] for s in heldout}
    assert not ids_t & ids_h and ids_t | ids_h == {s["id"] for s in scenarios}
    assert ev.select_split(scenarios, "all") == scenarios
    with pytest.raises(ValueError):
        ev.select_split(scenarios, "nope")


async def test_rules_fallback_rows_are_not_scored_as_the_model(scenarios):
    """A run with no model answer is labelled rules, shown, and left out of the live metrics."""
    answers = [(None, "rules"), (GOOD, "live")]

    async def call(system, prompt, live_allowed=True):
        return answers.pop(0) if answers else (GOOD, "live")

    res = await ev.run_eval(
        provider="live", runs=2, max_calls=5, scenarios=[by_id(scenarios, "s09-swap-target-refuses")],
        live_call=call,
    )  # fmt: skip
    assert res["runs_completed"] == 2 and res["runs_scored"] == 1 and res["rules_fallback_runs"] == 1
    # the rules fallback would agree with the label here; the model's GOOD (with a refused swap) does not
    assert res["preferred_top_agreement"] == 0.0 and res["modes"] == {"rules": 1, "live": 1}


def test_null_commitment_date_has_its_own_reason(scenarios):
    sc = by_id(scenarios, "s01-payday-friday-hi")
    text = json.dumps({"commitment": {"amount": "200.00", "date": None}, "remedies": []})
    proposal = ev.parse_live(text, sc, ev.build_charter(sc))
    assert proposal.commitment is None and "no date" in proposal.commitment_rejected
