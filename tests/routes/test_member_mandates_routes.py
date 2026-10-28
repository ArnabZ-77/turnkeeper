"""Member mandates over HTTP: GET mandates, POST member-agents/run, the Judge actions (toggle, try_overcharge)
and the opt-in Story B. Flag off => 404 / unchanged. PayPal, the toolkit and the LLM are faked."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from recovery_fakes import FakeInvoiceToolkit  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.domain import member_mandate as mm  # noqa: E402
from app.engine import member_mandates, recovery, state, story  # noqa: E402

ORDER = list(story.STEP_NAMES)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    monkeypatch.setenv("TK_LLM_API_KEY", "")
    monkeypatch.delenv("TK_MEMBER_MANDATES", raising=False)
    monkeypatch.delenv("TK_MEMBER_AGENTS", raising=False)
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


def _wait_done(client: TestClient, step: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get("/api/judge/story").json()
        if {s["step"]: s["status"] for s in view["steps"]}[step] == "done":
            return
        time.sleep(0.05)
    raise AssertionError(f"story step {step} did not finish")


def _next(client: TestClient, expected: str, **params) -> dict:
    r = client.post("/api/judge/story/next", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step"] == expected
    if body.get("task_id"):
        _wait_done(client, expected)
        body["story"] = client.get("/api/judge/story").json()
    return body


def _enable(client, cid: str) -> None:
    r = client.post("/api/judge/toggle_member_mandates", json={"circle_id": cid, "on": True})
    assert r.status_code == 200, r.text


def _to_voting(client, toolkit) -> str:
    """Story steps 1-5 (default path) on a fresh circle; returns its id."""
    cid = _next(client, "reset")["session"]["circle_id"]
    for step in ("advance_to_t3", "run_round3", "reply_as_ravi", "start_recovery"):
        _next(client, step)
    return cid


# --- flag off ---------------------------------------------------------------------------------------------------


def test_routes_are_404_when_the_flag_is_off(client, session):
    cid = session["circle_id"]
    r = client.get(f"/api/circles/{cid}/mandates")
    assert r.status_code == 404 and "off" in r.json()["detail"]
    assert client.post(f"/api/circles/{cid}/member-agents/run").status_code == 404
    r = client.post("/api/judge/try_overcharge", json={"circle_id": cid})
    assert r.status_code == 404


def test_routes_need_a_session(client):
    assert client.get("/api/circles/abcdef/mandates").status_code == 401
    assert client.post("/api/circles/abcdef/member-agents/run").status_code == 401


def test_other_circles_are_not_reachable(client, session):
    assert client.get("/api/circles/zzzzzz/mandates").status_code in (403, 404)


def test_kill_switch_makes_everything_404(client, session, monkeypatch):
    cid = session["circle_id"]
    _enable(client, cid)
    assert client.get(f"/api/circles/{cid}/mandates").status_code == 200
    monkeypatch.setenv("TK_MEMBER_AGENTS", "0")
    get_settings.cache_clear()
    r = client.get(f"/api/circles/{cid}/mandates")
    assert r.status_code == 404 and "TK_MEMBER_AGENTS=0" in r.json()["detail"]
    assert (
        client.post("/api/judge/toggle_member_mandates", json={"circle_id": cid, "on": True}).status_code
        == 404
    )
    assert client.post("/api/judge/try_overcharge", json={"circle_id": cid}).status_code == 404


def test_board_json_has_no_mandate_surface_while_off(client, session):
    board = client.get(f"/api/circles/{session['circle_id']}").json()
    assert "member_mandates" not in board["circle"]["flags"]
    assert "mandate" not in " ".join(k for k in board if "member" in k)


def test_default_story_start_is_unchanged_and_has_no_variant(client, session):
    body = _next(client, "reset")
    assert "variant" not in body["story"]
    assert body["story"]["steps"][ORDER.index("consents")]["label"].startswith(
        "Consent as the affected members"
    )
    assert state.is_story_b(state.load_circle(body["session"]["circle_id"])) is False
    assert member_mandates.enabled(body["session"]["circle_id"]) is False


def test_unknown_story_variant_is_rejected(client, session):
    r = client.post("/api/judge/story/reset", params={"variant": "nope"})
    assert r.status_code == 422


def test_variant_only_with_reset(client, session):
    _next(client, "reset")
    r = client.post("/api/judge/story/next", params={"variant": "member_agents"})
    assert r.status_code == 422 and "reset" in r.json()["detail"]


# --- flag on ----------------------------------------------------------------------------------------------------


def test_toggle_seeds_and_lists_mandates(client, session):
    cid = session["circle_id"]
    r = client.post("/api/judge/toggle_member_mandates", json={"circle_id": cid, "on": True})
    assert r.json() == {"ok": True, "action": "toggle_member_mandates", "enabled": True, "seeded": 6}
    body = client.get(f"/api/circles/{cid}/mandates").json()
    assert body["enabled"] is True and len(body["members"]) == 6
    assert body["label"] == mm.MANDATE_LABEL and body["signature_label"] == mm.SIGNATURE_LABEL
    assert all(m["signature_verified"] and m["max_charge_per_round"] == "200.00" for m in body["members"])
    flags = client.get(f"/api/circles/{cid}").json()["circle"]["flags"]
    assert flags["member_mandates"] is True


def test_toggle_off_goes_back_to_404(client, session):
    cid = session["circle_id"]
    _enable(client, cid)
    r = client.post("/api/judge/toggle_member_mandates", json={"circle_id": cid, "on": False})
    assert r.json()["enabled"] is False
    assert client.get(f"/api/circles/{cid}/mandates").status_code == 404


def test_toggle_without_value_flips(client, session):
    cid = session["circle_id"]
    assert client.post("/api/judge/toggle_member_mandates", json={"circle_id": cid}).json()["enabled"] is True
    assert (
        client.post("/api/judge/toggle_member_mandates", json={"circle_id": cid}).json()["enabled"] is False
    )


def test_mandates_never_leak_a_full_signature(client, session):
    cid = session["circle_id"]
    _enable(client, cid)
    text = client.get(f"/api/circles/{cid}/mandates").text
    full = member_mandates.load_mandate(cid, "ravi").signature
    assert full not in text


def test_try_overcharge_is_denied_before_paypal(client, session, paypal):
    cid = session["circle_id"]
    _enable(client, cid)
    r = client.post("/api/judge/try_overcharge", json={"circle_id": cid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["denied"] is True and body["paypal_called"] is False
    assert body["reason"] == "charge 230.00 exceeds ravi's mandate limit 200.00 per round"
    assert body["banner"] == f"DENIED before PayPal is called: {body['reason']}"
    assert body["amount"] == "230.00" and body["member"] == "ravi"
    assert paypal.charges == [] and paypal.payouts == []


def test_try_overcharge_with_a_legal_amount_is_allowed_but_sends_nothing(client, session, paypal):
    cid = session["circle_id"]
    _enable(client, cid)
    body = client.post(
        "/api/judge/try_overcharge", json={"circle_id": cid, "amount": "200.00", "member": "asha"}
    ).json()
    assert body["denied"] is False and body["ticket"] and body["paypal_called"] is False
    assert paypal.charges == []


def test_try_overcharge_rejects_a_bad_amount_with_a_reason(client, session):
    cid = session["circle_id"]
    _enable(client, cid)
    body = client.post("/api/judge/try_overcharge", json={"circle_id": cid, "amount": "abc"}).json()
    assert body["denied"] is True and "not a valid money value" in body["reason"]


def test_try_overcharge_unknown_member_422_and_unknown_round_404(client, session):
    cid = session["circle_id"]
    _enable(client, cid)
    assert (
        client.post("/api/judge/try_overcharge", json={"circle_id": cid, "member": "Bad Handle"}).status_code
        == 422
    )
    assert client.post("/api/judge/try_overcharge", json={"circle_id": cid, "round_n": 99}).status_code == 404
    body = client.post("/api/judge/try_overcharge", json={"circle_id": cid, "member": "nobody"}).json()
    assert body["denied"] is True and "unknown member" in body["reason"]


def test_run_with_no_vote_open_is_409(client, session):
    cid = session["circle_id"]
    _enable(client, cid)
    r = client.post(f"/api/circles/{cid}/member-agents/run")
    assert r.status_code == 409


def test_run_rejects_bad_answers(client, session):
    cid = session["circle_id"]
    _enable(client, cid)
    r = client.post(f"/api/circles/{cid}/member-agents/run", json={"answers": {"ravi": "maybe"}})
    assert r.status_code == 422
    r = client.post(f"/api/circles/{cid}/member-agents/run", json={"set_id": "bad"})
    assert r.status_code == 422


def test_run_over_http_decides_and_asks(client, session, toolkit):
    cid = _to_voting(client, toolkit)
    _enable(client, cid)
    r = client.post(f"/api/circles/{cid}/member-agents/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "member_agents_run" and "ravi" in body["asked"]
    tomas = next(d for d in body["decisions"] if d["member"] == "tomas" and d["kind"] == "consent")
    assert tomas["choice"] == "yes" and tomas["source"] == "agent"
    # the human's tap answers the ask
    r = client.post(f"/api/circles/{cid}/member-agents/run", json={"answers": {"ravi": "yes"}})
    assert r.status_code == 200 and sorted(r.json()["consents"]["given"]) == ["ravi", "tomas"]


def test_a_direct_run_taints_the_story_recording(client, session, toolkit):
    cid = _to_voting(client, toolkit)
    _enable(client, cid)
    client.post(f"/api/circles/{cid}/member-agents/run")
    assert story.load(cid).tainted is True  # not the canonical public replay


# --- Story B ----------------------------------------------------------------------------------------------------


def test_story_b_runs_end_to_end_with_member_agents(client, session, toolkit):
    body = _next(client, "reset", variant="member_agents")
    cid = body["session"]["circle_id"]
    assert body["story"]["variant"] == "member_agents"
    assert member_mandates.enabled(cid) and state.is_story_b(state.load_circle(cid))
    assert len(member_mandates.mandates_view(cid)["members"]) == 6
    labels = {s["step"]: s["label"] for s in body["story"]["steps"]}
    assert "Member agents answer consents" in labels["consents"] and "Member agents vote" in labels["votes"]
    for step in ("advance_to_t3", "run_round3", "reply_as_ravi", "start_recovery"):
        _next(client, step)
    consents = _next(client, "consents")
    assert consents["result"]["variant"] == "member_agents"
    by = {d["member"]: d for d in consents["result"]["decisions"]}
    assert by["tomas"]["choice"] == "yes" and by["tomas"]["source"] == "agent"
    assert (
        by["ravi"]["choice"] == "ask" and by["ravi"]["source"] == "judge"
    )  # the judge's click, recorded as such
    assert "Tomas" in consents["summary"] and "agent" in consents["summary"]
    assert "asked, answered yes by the judge's click" in consents["summary"]  # labelled: the human tap
    assert consents["paypal"] is None and consents["ai"] in ("rules", "replay", "live")
    votes = _next(client, "votes")
    assert votes["result"]["votes"]["passed"] is True and "by member agents" in votes["summary"]
    rec = {(d["kind"], d["member"]): d for d in votes["result"]["decisions"]}
    assert all(d["source"] == "agent" for d in rec.values() if d["recorded"])
    for step in ("approve", "execute", "payout", "clean_round4"):
        _next(client, step)
    final = client.get("/api/judge/story").json()
    assert final["complete"] is True and final["variant"] == "member_agents"
    board = client.get(f"/api/circles/{cid}").json()
    r3 = {c["member"]: c["status"] for c in board["contributions"] if c["round_n"] == 3}
    assert r3["ravi"] == "COVERED"
    assert board["circle"]["flags"]["member_mandates"] is True


def test_story_b_is_never_the_public_replay(client, session, toolkit):
    _next(client, "reset", variant="member_agents")
    cid = client.get("/api/session").json()["circle_id"]
    row = story.load(cid)
    assert row is not None
    for step in ORDER[1:]:
        _next(client, step)
    shipped = client.get("/api/replay").json()
    assert shipped["source"] == "shipped"  # Story B never replaces the canonical recording


def test_story_b_second_reset_without_variant_goes_back_to_the_default(client, session):
    first = _next(client, "reset", variant="member_agents")
    assert first["story"]["variant"] == "member_agents"
    again = client.post("/api/judge/story/reset")
    assert again.status_code == 200
    new_cid = again.json()["session"]["circle_id"]
    assert "variant" not in again.json()["story"]
    assert member_mandates.enabled(new_cid) is False


def test_story_b_with_the_kill_switch_is_404(client, session, monkeypatch):
    monkeypatch.setenv("TK_MEMBER_AGENTS", "0")
    get_settings.cache_clear()
    r = client.post("/api/judge/story/reset", params={"variant": "member_agents"})
    assert r.status_code == 404
