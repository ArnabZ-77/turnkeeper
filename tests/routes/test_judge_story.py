"""Judge Mode story ("Next step"), the read-only replay route and the UNCLAIMED re-route action.

PayPal, the invoice toolkit and the LLM are faked; nothing reaches the network.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from fakes import vault_members  # noqa: E402
from recovery_fakes import FakeInvoiceToolkit  # noqa: E402
from reroute_fakes import RerouteFake  # noqa: E402

from app.engine import recovery, rounds, state, story  # noqa: E402
from routes.conftest import wait_for_task  # noqa: E402

ORDER = list(story.STEP_NAMES)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    monkeypatch.setenv("TK_LLM_API_KEY", "")
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


def _story(client: TestClient) -> dict:
    r = client.get("/api/judge/story")
    assert r.status_code == 200, r.text
    return r.json()


def _wait_step_done(client: TestClient, step: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = _story(client)
        st = {s["step"]: s["status"] for s in view["steps"]}
        if st[step] == "done":
            return view
        if view["running"] is None and st[step] == "next" and view["next"]["step"] == step:
            raise AssertionError(f"story step {step} finished without success: {view}")
        time.sleep(0.05)
    raise AssertionError(f"story step {step} did not finish")


def _next(client: TestClient, expected: str) -> dict:
    r = client.post("/api/judge/story/next")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step"] == expected
    if body.get("task_id"):
        assert body["story"]["running"] == {"step": expected, "task_id": body["task_id"]}
        _wait_step_done(client, expected)
    return body


def test_story_needs_a_session(client):
    assert client.get("/api/judge/story").status_code == 401
    assert client.post("/api/judge/story/next").status_code == 401


def test_story_view_and_order(client, session):
    view = _story(client)
    assert view["next"]["step"] == "reset" and view["current"] is None and view["complete"] is False
    assert view["next"]["action_label"].startswith("Next: Reset demo")
    assert [s["step"] for s in view["steps"]] == ORDER and view["paypal"] == "simulated"
    assert view["replay_url"] == "/api/replay"
    assert client.post("/api/judge/story/explode").status_code == 404
    r = client.post("/api/judge/story/votes")
    assert r.status_code == 409 and "next story step is 'reset'" in r.json()["detail"]
    body = _next(client, "reset")
    assert body["session"]["circle_id"] != session["circle_id"]
    assert body["story"]["current"] == "reset" and body["story"]["next"]["step"] == "advance_to_t3"
    # the simulated wording when the sandbox cannot charge this circle
    labels = {s["step"]: s["label"] for s in body["story"]["steps"]}
    assert labels["run_round3"].startswith("Simulate Round 3")


def test_simulated_story_end_to_end_and_replay(client, session, toolkit):
    shipped = client.get("/api/replay")  # a fresh deployment serves the shipped (simulated) recording
    assert shipped.status_code == 200 and shipped.json()["source"] == "shipped"
    assert shipped.json()["paypal_mode"] == "simulated"
    cid = _next(client, "reset")["session"]["circle_id"]
    t3 = _next(client, "advance_to_t3")
    assert "ravi" in t3["result"]["flagged"]
    r3 = _next(client, "run_round3")
    assert r3["mode"] == "simulated" and r3["result"]["simulated"] is True
    reply = _next(client, "reply_as_ravi")
    assert reply["result"]["payday"]
    _next(client, "start_recovery")
    assert recovery.latest_set(cid).status == "voting"
    consents = _next(client, "consents")
    assert sorted(consents["result"]["consented"]) == ["ravi", "tomas"]
    _next(client, "votes")
    _next(client, "approve")
    assert recovery.latest_set(cid).status == "approved"
    ex = _next(client, "execute")
    assert ex["result"]["status"] == "executed"
    pay = _next(client, "payout")
    assert pay["mode"] == "simulated" and "skipped" in pay["result"]
    last = _next(client, "clean_round4")
    assert last["story"]["complete"] is True and last["story"]["next"] is None

    board = client.get(f"/api/circles/{cid}").json()
    r3_rows = {c["member"]: c["status"] for c in board["contributions"] if c["round_n"] == 3}
    assert r3_rows["ravi"] == "COVERED"
    assert all(c["status"] == "COMPLETED" for c in board["contributions"] if c["round_n"] == 4)
    assert board["rounds"][2]["payout"] is None and board["rounds"][3]["payout"] is None  # no money moved

    r = client.post("/api/judge/story/next")
    assert r.status_code == 409 and "complete" in r.json()["detail"]

    replay = client.get("/api/replay")
    assert replay.status_code == 200
    data = replay.json()
    assert data["read_only"] is True and data["paypal_mode"] == "simulated" and data["source"] == "recorded"
    assert [s["step"] for s in data["steps"]] == ORDER
    run3 = data["steps"][ORDER.index("run_round3")]
    assert any(e["type"] == "chip" and e["member"] == "ravi" for e in run3["events"])
    assert data["steps"][0]["events"][0]["type"] == "story"
    assert "@" not in json.dumps(data)
    # the replay is public: a cookieless client can read it
    with TestClient(client.app) as anon:
        assert anon.get("/api/replay").status_code == 200


def test_live_story_with_faked_sandbox(client, session, toolkit, monkeypatch):
    from fakes import FakePayPal

    paypal = FakePayPal().install(monkeypatch)
    cid = _next(client, "reset")["session"]["circle_id"]
    vault_members(cid)
    view = _story(client)
    assert view["paypal"] == "live"
    _next(client, "advance_to_t3")
    _next(client, "run_round3")
    assert any(c["mock_decline"] for c in paypal.charges)
    for step in ("reply_as_ravi", "start_recovery", "consents", "votes", "approve", "execute"):
        _next(client, step)
    pay = _next(client, "payout")
    assert pay["mode"] == "live" and pay["result"]["status"] == "SUCCESS"
    assert (
        pay["result"]["recipient"] == "dev" and pay["result"]["paypal_balance"]["source"] == "paypal_balances"
    )
    _next(client, "clean_round4")
    board = client.get(f"/api/circles/{cid}").json()
    assert board["rounds"][3]["status"] == "PAID" and board["circle"]["flags"]["ravi_declines"] is False
    assert board["circle"]["paypal_balance"]["value"] == "5000.00"
    assert len(paypal.payouts) == 2
    data = client.get("/api/replay").json()
    assert data["paypal_mode"] == "live" and len(data["steps"]) == len(ORDER)


def test_failed_background_step_stays_next(client, session, monkeypatch):
    """A round run that does not end with ravi DECLINED is not counted; the step can be retried."""
    from fakes import FakePayPal

    FakePayPal().install(monkeypatch)
    cid = _next(client, "reset")["session"]["circle_id"]
    vault_members(cid)
    _next(client, "advance_to_t3")
    state.set_flag(cid, "ravi_declines", False)
    monkeypatch.setattr(state, "set_flag", lambda *a, **k: {})  # the step cannot switch the decline back on
    r = client.post("/api/judge/story/next")
    assert r.status_code == 200 and r.json()["task_id"]
    wait_for_task(client, r.json()["task_id"])
    view = _story(client)
    assert view["next"]["step"] == "run_round3" and view["running"] is None
    assert {s["step"]: s["status"] for s in view["steps"]}["run_round3"] == "next"


def test_waitlist_points_at_the_replay(client, session, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TK_MAX_ACTIVE_SESSIONS", "1")
    get_settings.cache_clear()
    with TestClient(client.app) as other:
        r = other.get("/api/session")
    assert r.status_code == 429
    body = r.json()
    assert (
        body["waitlist"] is True and body["replay_url"] == "/api/replay" and body["replay_available"] is True
    )  # the shipped recording (app/fixtures/replay_canonical.json) is there before any story completes


def test_replay_is_404_without_any_recording(client, session, monkeypatch, tmp_path):
    monkeypatch.setattr(story, "SHIPPED_REPLAY", tmp_path / "missing.json")
    assert client.get("/api/replay").status_code == 404


def test_shipped_replay_is_a_complete_simulated_run():
    data = json.loads(story.SHIPPED_REPLAY.read_text(encoding="utf-8"))
    assert data["paypal_mode"] == "simulated" and [s["step"] for s in data["steps"]] == ORDER
    assert all(s["mode"] in ("judge", "rules", "simulated") for s in data["steps"])  # nothing claims "live"
    assert "@" not in json.dumps(data)


def test_judge_reroute_unclaimed_action(client, vaulted, monkeypatch):
    fake = RerouteFake(cancel="fail500", batch_script=("SUCCESS",)).install(monkeypatch)
    cid = vaulted["circle_id"]
    r = client.post("/api/judge/reroute_unclaimed", json={"circle_id": cid})
    assert r.status_code == 409  # nothing UNCLAIMED yet

    import asyncio

    async def _setup() -> None:
        async def noop(ev):
            return None

        state.set_flag(cid, "ravi_declines", False)
        state.set_flag(cid, "next_payout_unclaimed", True)
        await rounds.collect_round(cid, 3, publish=noop)
        await rounds.pay_round(cid, 3, publish=noop)

    asyncio.run(_setup())
    r = client.post("/api/judge/reroute_unclaimed", json={"circle_id": cid})
    assert r.status_code == 200, r.text
    assert r.json()["round_n"] == 3
    done = wait_for_task(client, r.json()["task_id"])
    assert done["ok"] is True and "cancel cancel_failed" in done["summary"]
    board = client.get(f"/api/circles/{cid}").json()
    r3 = board["rounds"][2]
    assert r3["payout_rev"] == 2 and r3["status"] == "PAID"
    assert r3["pending_return"]["label"] == "pending return"
    assert fake.cancel_calls and len(fake.payouts) == 2


# --- fixer regressions ---------------------------------------------------------------------------------------


def _sid(client: TestClient, session: dict) -> str:
    from app.engine import sessions

    return session.get("sid") or client.cookies.get(sessions.COOKIE_NAME)


def test_restart_works_while_a_background_run_is_going(client, session):
    """Restart must always work; a run left on the old circle stops reaching the session's stream."""
    import asyncio

    from app.engine import tasks
    from app.engine.events import get_event_bus

    sid = _sid(client, session)
    old = _next(client, "reset")["session"]["circle_id"]
    stuck = tasks.TaskRecord(task_id="stuck0001", kind="reroute", circle_id=old, round_n=3, sid=sid)
    tasks._TASKS[stuck.task_id] = stuck  # e.g. a re-route waiting up to 60 s, or a hung provider call
    publish_old = tasks.make_publisher(sid, old, "run-old")

    r = client.post("/api/judge/story/reset")
    assert r.status_code == 200, r.text
    new = r.json()["session"]["circle_id"]
    assert new != old and r.json()["story"]["current"] == "reset"

    asyncio.run(publish_old({"type": "toast", "level": "info", "text": "late event from the old circle"}))
    texts = [e.get("text") for e in get_event_bus().recent(sid, 200)]
    assert "late event from the old circle" not in texts
    publish_new = tasks.make_publisher(sid, new, "run-new")
    asyncio.run(publish_new({"type": "toast", "level": "info", "text": "event on the new circle"}))
    assert "event on the new circle" in [e.get("text") for e in get_event_bus().recent(sid, 200)]


def test_story_error_is_an_http_error_not_a_500(client, session, monkeypatch):
    _next(client, "reset")

    def out_of_order(*_a, **_k):
        raise story.StoryError(409, "story step 'advance_to_t3' is out of order (next is 'x')")

    monkeypatch.setattr(story, "record_step", out_of_order)
    r = client.post("/api/judge/story/next")
    assert r.status_code == 409 and "out of order" in r.json()["detail"]


async def test_concurrent_story_posts_run_the_step_once(client, session, monkeypatch):
    import asyncio

    from fastapi import HTTPException
    from starlette.requests import Request

    from app.engine import sessions
    from app.routes import judge_story

    _next(client, "reset")
    sess = sessions.resolve_session(_sid(client, session))
    calls: list[str] = []

    async def slow_step(_sess, _request):
        calls.append("run")
        await asyncio.sleep(0.05)
        return {"ok": True}, None, "judge", "slow step"

    monkeypatch.setitem(judge_story.EXECUTORS, "advance_to_t3", slow_step)
    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("1.2.3.4", 1)}
    )
    results = await asyncio.gather(
        judge_story.post_story("advance_to_t3", sess, request),
        judge_story.post_story("advance_to_t3", sess, request),
        return_exceptions=True,
    )
    assert calls == ["run"]
    errors = [r for r in results if isinstance(r, HTTPException)]
    assert len(errors) == 1 and errors[0].status_code == 409 and "already running" in errors[0].detail
    assert sum(1 for r in results if isinstance(r, dict) and r["step"] == "advance_to_t3") == 1


def test_already_paid_payout_step_is_not_labelled_live(client, session, monkeypatch):
    from app.db import session_scope
    from app.models import Round
    from app.routes import judge_story

    cid = _next(client, "reset")["session"]["circle_id"]
    with session_scope() as db:
        rnd = db.exec(__import__("sqlmodel").select(Round).where(Round.circle_id == cid, Round.n == 3)).one()
        rnd.status, rnd.payout_status = "PAID", "SUCCESS"
        db.add(rnd)
    from app.engine import sessions

    sess = sessions.resolve_session(_sid(client, session))
    import asyncio

    result, task_id, mode, summary = asyncio.run(judge_story._step_payout(sess, None))
    assert mode == "judge" and task_id is None and result["already"] is True
    assert summary == "Round 3 was already paid (no new payout)"
