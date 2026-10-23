"""Forecaster backtest: deterministic, 60 member-rounds, planted late payers, the production scorer."""

import json

from eval import forecast_backtest as bt


def test_backtest_shape_and_determinism():
    a, b = bt.run(), bt.run()
    assert a == b
    assert a["member_rounds"] == 60 and a["members"] == 20
    assert a["hits"] + a["misses"] + a["false_alarms"] + a["correct_rejections"] == 60
    assert a["threshold"] == 0.5
    planted = a["planted_late_payers"]
    assert planted["members"] == 4 and planted["member_rounds"] == 12
    assert len(a["misses_detail"]) == a["misses"] and len(a["false_alarms_detail"]) == a["false_alarms"]


def test_backtest_beats_the_base_rate():
    r = bt.run()
    assert r["precision"] > r["late_base_rate"]  # flagging means something
    assert r["planted_late_payers"]["flagged"] >= 9  # most planted late payers are caught
    assert r["by_persona"]["reliable"]["flagged"] == 0


def test_other_seed_changes_the_draws():
    assert bt.run(seed=1) != bt.run()


def test_cli_writes_json(tmp_path):
    out = tmp_path / "f.json"
    assert bt.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["member_rounds"] == 60


def test_committed_results_match_the_code():
    committed = json.loads((bt.OUT).read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(bt.run(), ensure_ascii=False))
