"""Flag-off regression: with ``member_mandates`` off, the default Judge story and the board JSON are exactly
what they were before the feature existed.

``golden/default_story_flag_off.json`` was generated from the code BEFORE the member-mandate work (HEAD of
2026-10-27) with ``TK_WRITE_GOLDEN=1``. Volatile values (circle id, run id, ids, timestamps) are normalised.
PayPal, the toolkit and the LLM are faked; nothing reaches the network.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from recovery_fakes import FakeInvoiceToolkit  # noqa: E402

from app.engine import recovery, state, story  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden" / "default_story_flag_off.json"
_TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?")
_IDS = re.compile(r"\b(rs|rm)_[0-9a-f]{8}\b")
_UUID = re.compile(r"\b[0-9a-f]{32}\b|\b[0-9a-f]{16}\b")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    monkeypatch.setenv("TK_LLM_API_KEY", "")
    monkeypatch.delenv("TK_MEMBER_MANDATES", raising=False)
    get_settings.cache_clear()
    state.clear_paypal_balance()
    yield
    state.clear_paypal_balance()
    get_settings.cache_clear()


@pytest.fixture
def toolkit(monkeypatch) -> FakeInvoiceToolkit:
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    return tk


def _norm(value, subs: dict[str, str]):
    if isinstance(value, dict):
        return {k: _norm(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v, subs) for v in value]
    if isinstance(value, str):
        for raw, tag in subs.items():
            value = value.replace(raw, tag)
        value = _TS.sub("<ts>", value)
        value = _IDS.sub(lambda m: f"<{m.group(1)}_id>", value)
        return _UUID.sub("<uuid>", value)
    return value


def _wait_step_done(client, step: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get("/api/judge/story").json()
        if {s["step"]: s["status"] for s in view["steps"]}[step] == "done":
            return
        time.sleep(0.05)
    raise AssertionError(f"story step {step} did not finish")


def run_default_story(client) -> dict:
    """Walk the whole default story and snapshot steps + final board (volatile values normalised)."""
    r = client.post("/api/judge/story/next")
    assert r.status_code == 200, r.text
    cid = r.json()["session"]["circle_id"]
    run_id = r.json()["story"]["run_id"]
    subs = {cid: "<circle>", run_id: "<run>"}
    steps = []
    view = r.json()["story"]
    for _ in range(len(story.STEP_NAMES) - 1):
        r = client.post("/api/judge/story/next")
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("task_id"):
            _wait_step_done(client, body["step"])
            body["story"] = client.get("/api/judge/story").json()
        steps.append(
            {
                "step": body["step"],
                "mode": body["mode"],
                "paypal": body["paypal"],
                "ai": body["ai"],
                "summary": body["summary"],
                "result": body["result"],
            }
        )
        view = body["story"]
    board = client.get(f"/api/circles/{cid}").json()
    return _norm(
        {
            "steps": steps,
            "story_steps": [{k: s[k] for k in ("step", "label", "status", "mode")} for s in view["steps"]],
            "board": board,
        },
        subs,
    )


def test_default_story_and_board_match_the_pre_feature_baseline(client, session, toolkit):
    snap = run_default_story(client)
    if os.environ.get("TK_WRITE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(snap, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        pytest.skip("golden written")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # compare through a JSON round trip so tuples/Decimals read the same on both sides
    assert json.loads(json.dumps(snap, sort_keys=True, default=str)) == golden


def test_golden_board_has_no_member_mandate_surface():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden["board"]["circle"]["flags"]) == {"ravi_declines", "next_payout_unclaimed"}
    assert "member_mandates" not in json.dumps(golden)
