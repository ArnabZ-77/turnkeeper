"""POST .../collect and .../payout: background agent task, session publish function, rules mode end to end."""

import asyncio
from decimal import Decimal

from app.engine import state
from app.engine.events import get_event_bus
from app.engine.rounds import MOCK_DECLINE_LABEL
from routes.conftest import wait_for_task


def _tool_calls(circle_id: str) -> list[str]:
    """Tool names in call order, from the AgentTrace rows the session publisher persists."""
    import json

    from sqlmodel import select

    from app.db import session_scope
    from app.models import AgentTrace

    with session_scope() as db:
        rows = db.exec(
            select(AgentTrace).where(AgentTrace.circle_id == circle_id).order_by(AgentTrace.id)
        ).all()
    events = [json.loads(r.payload) for r in rows]
    assert all(e.get("mode") == "rules" for e in events if e.get("kind") in ("tool_call", "tool_result"))
    return [e["tool"] for e in events if e.get("kind") == "tool_call"]


def test_collect_requires_a_session(client):
    assert client.post("/api/circles/abcdef/rounds/3/collect").status_code == 401


def test_collect_rejects_other_circles_and_unknown_rounds(client, session):
    assert client.post("/api/circles/zzzzzz/rounds/3/collect").status_code == 403
    assert client.post(f"/api/circles/{session['circle_id']}/rounds/99/collect").status_code == 404


def test_collect_runs_run_task_in_the_background_with_the_session_publisher(client, session, monkeypatch):
    import app.agent.treasurer as treasurer

    seen: dict = {}

    async def fake_run_task(task, *, publish, client=None, live_allowed=True):
        seen["task"] = task
        await publish({"type": "trace", "kind": "text", "text": "hello from the fake agent"})
        await asyncio.sleep(0.05)
        return treasurer.AgentRunResult(ok=True, mode="rules", summary="fake", tool_calls=0, trace=[])

    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    r = client.post(f"/api/circles/{session['circle_id']}/rounds/3/collect")
    assert r.status_code == 202
    body = r.json()
    assert body["kind"] == "collect_round" and body["agent"] is True and body["task_id"]
    status = client.get(f"/api/tasks/{body['task_id']}").json()
    assert status["task_id"] == body["task_id"]  # returned immediately; the run continues in the background
    done = wait_for_task(client, body["task_id"])
    assert done["ok"] is True and done["mode"] == "rules" and done["summary"] == "fake"

    task = seen["task"]
    assert isinstance(task, treasurer.AgentTask)
    assert task.kind == "collect_round" and task.circle_id == session["circle_id"]
    assert (
        task.round_n == 3 and task.sid == session["sid"] and "run_id" not in task.context
    )  # keeps replays cross-session

    recent = get_event_bus().recent(session["sid"])
    assert any(e["type"] == "trace" and e["text"] == "hello from the fake agent" for e in recent)
    assert recent[-1]["type"] == "board"
    from sqlmodel import select

    from app.db import session_scope
    from app.models import AgentTrace

    with session_scope() as db:
        traces = db.exec(select(AgentTrace).where(AgentTrace.circle_id == session["circle_id"])).all()
    assert traces and traces[0].run_id == session["run_id"] and "fake agent" in traces[0].payload


def test_payout_route_uses_kind_payout(client, session, monkeypatch):
    import app.agent.treasurer as treasurer

    kinds: list[str] = []

    async def fake_run_task(task, *, publish, client=None, live_allowed=True):
        kinds.append(task.kind)
        return treasurer.AgentRunResult(ok=False, mode="rules", summary="held", tool_calls=0, trace=[])

    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    r = client.post(f"/api/circles/{session['circle_id']}/rounds/3/payout")
    assert r.status_code == 202 and r.json()["kind"] == "payout"
    done = wait_for_task(client, r.json()["task_id"])
    assert done["ok"] is False and kinds == ["payout"]


def test_second_run_on_the_same_circle_is_409(client, session, monkeypatch):
    import app.agent.treasurer as treasurer

    release = asyncio.Event()

    async def slow_run_task(task, *, publish, client=None, live_allowed=True):
        await release.wait()
        return treasurer.AgentRunResult(ok=True, mode="rules", summary="", tool_calls=0, trace=[])

    monkeypatch.setattr(treasurer, "run_task", slow_run_task)
    first = client.post(f"/api/circles/{session['circle_id']}/rounds/3/collect")
    assert first.status_code == 202
    second = client.post(f"/api/circles/{session['circle_id']}/rounds/3/collect")
    assert second.status_code == 409 and second.json()["detail"]["task_id"] == first.json()["task_id"]
    client.portal.call(release.set)
    wait_for_task(client, first.json()["task_id"])


def test_agent_failure_is_surfaced_not_lost(client, session, monkeypatch):
    import app.agent.treasurer as treasurer

    async def broken(task, *, publish, client=None, live_allowed=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(treasurer, "run_task", broken)
    r = client.post(f"/api/circles/{session['circle_id']}/rounds/3/collect")
    done = wait_for_task(client, r.json()["task_id"])
    assert done["ok"] is False and done["error"].startswith("RuntimeError")
    assert "boom" not in done["error"]  # raw exception text stays in the server log
    recent = get_event_bus().recent(session["sid"])
    assert any(e["type"] == "toast" and e["level"] == "error" for e in recent)
    assert client.get("/api/tasks/nope").status_code == 404


def test_rules_mode_collect_end_to_end_with_ravi_declining(client, vaulted, paypal):
    circle_id = vaulted["circle_id"]
    r = client.post(f"/api/circles/{circle_id}/rounds/3/collect")
    assert r.status_code == 202
    done = wait_for_task(client, r.json()["task_id"])
    assert done["mode"] == "rules"
    assert "1 declined (ravi)" in done["summary"] and "Payout held: waiting on ravi" in done["summary"]

    board = client.get(f"/api/circles/{circle_id}").json()
    row = {c["member"]: c for c in board["contributions"] if c["round_n"] == 3}
    assert row["ravi"]["status"] == "DECLINED" and row["ravi"]["label"] == MOCK_DECLINE_LABEL
    assert row["ravi"]["decline_source"] == "api" and row["ravi"]["decline_code"] == "INSTRUMENT_DECLINED"
    completed = [h for h, c in row.items() if c["status"] == "COMPLETED"]
    assert sorted(completed) == ["asha", "dev", "lin", "meena", "tomas"]
    assert all(row[h]["label"] == "Confirmed via toolkit" for h in completed)  # get_order_details ran
    assert board["rounds"][2]["status"] == "COLLECTING" and board["rounds"][2]["payout"] is None
    assert paypal.payouts == [] and len(paypal.charges) == 6
    assert sorted(c[1]["order_id"] for c in paypal.toolkit.calls) == sorted(
        row[h]["order_id"] for h in completed
    )

    recent = get_event_bus().recent(vaulted["sid"], limit=200)
    types = [e["type"] for e in recent]
    assert types.count("chip") >= 6 and "trace" in types and "board" in types and "queue" in types
    tools = _tool_calls(circle_id)
    assert tools[0] == "collect_round" and "get_available_balance" in tools and tools[-1] == "record_note"
    assert tools.count("get_order_details") == 5 and "create_pot_payout" not in tools


def test_rules_mode_clean_round_collects_and_pays(client, vaulted, paypal):
    circle_id = vaulted["circle_id"]
    state.set_flag(circle_id, "ravi_declines", False)
    r = client.post(f"/api/circles/{circle_id}/rounds/3/collect")
    done = wait_for_task(client, r.json()["task_id"])
    assert done["ok"] is True and done["mode"] == "rules"
    assert "6 charged, 0 declined" in done["summary"] and "Pot paid" in done["summary"]

    board = client.get(f"/api/circles/{circle_id}").json()
    r3 = board["rounds"][2]
    pot = Decimal("192.53") * 6 - Decimal("0.25")
    assert (
        r3["status"] == "PAID" and r3["payout"]["status"] == "SUCCESS" and r3["payout"]["amount"] == str(pot)
    )
    assert len(paypal.payouts) == 1 and paypal.payouts[0]["items"][0]["receiver"] == "dev@example.com"
    assert board["counters"]["rounds_paid_on_time"] == 3
    tools = _tool_calls(circle_id)
    assert tools[0] == "collect_round" and tools[-1] == "record_note" and "create_pot_payout" in tools
    assert tools.count("get_order_details") == 6
    recent = get_event_bus().recent(vaulted["sid"], limit=200)
    payout_events = [e for e in recent if e["type"] == "payout"]
    assert (
        payout_events and payout_events[-1]["recipient"] == "dev" and payout_events[-1]["status"] == "SUCCESS"
    )


def test_rules_mode_payout_task_after_manual_collection(client, vaulted, paypal, collector):
    import asyncio as aio

    circle_id = vaulted["circle_id"]
    state.set_flag(circle_id, "ravi_declines", False)
    from app.engine import rounds

    aio.run(rounds.collect_round(circle_id, 3, publish=collector))
    r = client.post(f"/api/circles/{circle_id}/rounds/3/payout")
    done = wait_for_task(client, r.json()["task_id"])
    assert done["ok"] is True and "Pot paid" in done["summary"]
    assert client.get(f"/api/circles/{circle_id}").json()["rounds"][2]["status"] == "PAID"


def test_tasks_are_visible_only_to_their_session(app, client, session, monkeypatch):
    """M1 review: GET /api/tasks/{id} is bound to the session that started the run."""
    from fastapi.testclient import TestClient

    import app.agent.treasurer as treasurer

    async def fake_run_task(task, *, publish, client=None, live_allowed=True):
        return treasurer.AgentRunResult(ok=True, mode="rules", summary="fake", tool_calls=0, trace=[])

    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    task_id = client.post(f"/api/circles/{session['circle_id']}/rounds/3/collect").json()["task_id"]
    wait_for_task(client, task_id)
    with TestClient(app) as other:
        assert other.get(f"/api/tasks/{task_id}").status_code == 401  # no cookie
        other.get("/api/session")
        assert other.get(f"/api/tasks/{task_id}").status_code == 404  # someone else's task


def test_collect_without_paypal_credentials_is_503(client, session, monkeypatch):
    """M1 review: no sandbox credentials -> refuse the run instead of burning every member's attempt."""
    from app.config import get_settings

    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "")
    get_settings.cache_clear()
    r = client.post(f"/api/circles/{session['circle_id']}/rounds/3/collect")
    assert r.status_code == 503 and "not configured" in r.json()["detail"]
    board = client.get(f"/api/circles/{session['circle_id']}").json()
    assert all(c["attempt"] == 0 for c in board["contributions"] if c["round_n"] == 3)


def test_live_ai_budget_is_checked_once_per_run_and_passed_to_the_agent(client, session, monkeypatch):
    """With a model key, each run asks allow_live_call(ip); over the per-IP budget the agent gets
    live_allowed=False (replay, then rules mode). The IP never reaches the task context."""
    import app.agent.treasurer as treasurer
    from app.agent.llm import reset_live_calls
    from app.config import get_settings

    monkeypatch.setenv("TK_LLM_PROVIDER", "groq")
    monkeypatch.setenv("TK_LLM_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("TK_LIVE_LLM_PER_IP_HOUR", "1")
    get_settings.cache_clear()
    reset_live_calls()
    seen: list = []

    async def fake_run_task(task, *, publish, client=None, live_allowed=True):
        seen.append((live_allowed, dict(task.context)))
        return treasurer.AgentRunResult(ok=True, mode="rules", summary="fake", tool_calls=0, trace=[])

    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    try:
        assert client.get("/api/session").json()["live_ai"] is True
        for _ in range(2):
            r = client.post(f"/api/circles/{session['circle_id']}/rounds/3/collect")
            assert r.status_code == 202, r.text
            wait_for_task(client, r.json()["task_id"])
        assert [allowed for allowed, _ in seen] == [True, False]
        assert all("run_id" not in ctx for _, ctx in seen)
    finally:
        reset_live_calls()
        get_settings.cache_clear()
