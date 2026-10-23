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
