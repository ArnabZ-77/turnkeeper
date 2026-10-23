"""T-3 forecast job against the real engine on an in-memory DB, and the judge routes that trigger it.
No PayPal call is ever made (the forecast does not charge); the model is a fake provider or absent."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.agent.llm import LLMProvider, LLMTurn
from app.db import session_scope
from app.engine import forecast_job, inbox, state
from app.engine.clock import advance_clock
from app.models import Contribution, ForecastRow


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    from app.config import get_settings
    from app.db import reset_db
    from app.engine.events import reset_event_bus

    monkeypatch.setenv("TURNKEEPER_FREEZE", "0")
    get_settings.cache_clear()
    reset_db()
    reset_event_bus()
    yield
    get_settings.cache_clear()


@pytest.fixture
def cid() -> str:
    from app.engine.sessions import create_session

    return create_session(enforce_cap=False).circle_id


class Collector:
    def __init__(self):
        self.events: list[dict] = []

    async def __call__(self, ev):
        self.events.append(ev)

    def of(self, kind):
        return [e for e in self.events if e["type"] == kind]


class FakeProvider(LLMProvider):
    name = "groq"

    def __init__(self, script):
        super().__init__(model="fake-model", live=True)
        self.client = object()
        self.script = list(script)
        self.calls = 0

    async def chat(self, system, messages, tools, *, max_tokens):
        self.calls += 1
        return self.script.pop(0)


def _predicted(cid, n=3):
    with session_scope() as db:
        rows = db.exec(
            select(Contribution).where(Contribution.circle_id == cid, Contribution.round_n == n)
        ).all()
        return {c.member_handle: c.predicted for c in rows}


def test_fixture_starts_exactly_at_t_minus_3_of_round_3(cid):
    assert forecast_job.due_rounds(cid) == [3]


async def test_rules_forecast_flags_ravi_only(cid):
    pub = Collector()
    views = await forecast_job.on_clock_advanced(cid, publish=pub)
    assert {v["member"] for v in views} == {"asha", "ravi", "meena", "tomas", "lin", "dev"}
    flagged = [v for v in views if v["flagged"]]
    assert [v["member"] for v in flagged] == ["ravi"]
    ravi = flagged[0]
    assert ravi["score"] == pytest.approx(0.85) and ravi["mode"] == "rules" and ravi["lang"] == "hi"
    assert ravi["reason"].startswith("ravi at risk (0.85)") and "नमस्ते Ravi" in ravi["nudge"]
    assert ravi["nudge_en"].startswith("Hi Ravi")
    assert all(v["nudge"] is None for v in views if not v["flagged"])

    events = pub.of("forecast")
    assert len(events) == 1
    ev = events[0]
    assert {k: ev[k] for k in ("round_n", "member", "score", "mode")} == {
        "round_n": 3, "member": "ravi", "score": 0.85, "mode": "rules"
    }  # fmt: skip
    assert ev["reason"] == ravi["reason"] and ev["nudge"] == ravi["nudge"]
    assert pub.of("board") and pub.of("trace")[0]["source"] == "forecast"

    assert _predicted(cid) == {
        "asha": False,
        "ravi": True,
        "meena": False,
        "tomas": False,
        "lin": False,
        "dev": False,
    }
    board = state.board_model(cid)
    assert [f["member"] for f in board["forecasts"]][0] == "ravi"
    assert board["forecasts"][0]["factors"][0]["factor"] == "base"

    nudges = inbox.list_messages(cid, member="ravi", direction="out")
    assert len(nudges) == 1 and nudges[0]["round_n"] is None and nudges[0]["source"] == "rules"


async def test_forecast_runs_once_per_round(cid):
    await forecast_job.on_clock_advanced(cid)
    pub = Collector()
    assert await forecast_job.on_clock_advanced(cid, publish=pub) == []
    assert forecast_job.due_rounds(cid) == []
    assert pub.events == []
    again = await forecast_job.run_forecast(cid, 3)  # not forced: the stored forecast comes back
    assert len(again) == 6
    with session_scope() as db:
        assert len(db.exec(select(ForecastRow).where(ForecastRow.circle_id == cid)).all()) == 6


async def test_forced_rerun_replaces_rows_and_never_repeats_the_nudge(cid):
    await forecast_job.run_forecast(cid, 3)
    await forecast_job.run_forecast(cid, 3, force=True)
    with session_scope() as db:
        assert len(db.exec(select(ForecastRow).where(ForecastRow.circle_id == cid)).all()) == 6
    assert len(inbox.list_messages(cid, member="ravi", direction="out")) == 1


async def test_nudge_does_not_suppress_the_decline_notice(cid):
    from app.engine import recovery

    await forecast_job.run_forecast(cid, 3)
    notice = recovery.send_decline_notice(cid, 3, "ravi")
    assert notice is not None and notice["round_n"] == 3


async def test_model_wording_for_flagged_members_only(cid):
    reply = json.dumps(
        {
            "reason": "Late in rounds 1-2; payday after the due date.",
            "nudge": "नमस्ते {name}, याद है?",
            "nudge_en": "Hi {name}, remember?",
        }
    )
    p = FakeProvider([LLMTurn(text=reply, stop="end")])
    pub = Collector()
    views = await forecast_job.run_forecast(cid, 3, publish=pub, provider=p)
    assert p.calls == 1  # one call: only ravi is flagged
    ravi = next(v for v in views if v["member"] == "ravi")
    assert ravi["mode"] == "live" and ravi["nudge"] == "नमस्ते Ravi, याद है?"
    assert pub.of("forecast")[0]["mode"] == "live"
    assert inbox.list_messages(cid, member="ravi", direction="out")[0]["source"] == "agent"


async def test_model_budget_caps_explanations(cid, monkeypatch):
    monkeypatch.setattr(forecast_job, "MAX_MODEL_EXPLANATIONS", 0)
    p = FakeProvider([])
    views = await forecast_job.run_forecast(cid, 3, provider=p)
    assert p.calls == 0 and next(v for v in views if v["flagged"])["mode"] == "rules"


async def test_only_pending_contributions_change(cid):
    with session_scope() as db:
        c = db.exec(
            select(Contribution).where(
                Contribution.circle_id == cid, Contribution.round_n == 3, Contribution.member_handle == "ravi"
            )
        ).one()
        c.status, c.predicted = "COMPLETED", False
        db.add(c)
    await forecast_job.run_forecast(cid, 3)
    assert _predicted(cid)["ravi"] is False


async def test_clearing_a_stale_predicted_badge(cid):
    with session_scope() as db:
        c = db.exec(
            select(Contribution).where(
                Contribution.circle_id == cid, Contribution.round_n == 3, Contribution.member_handle == "asha"
            )
        ).one()
        c.predicted = True
        db.add(c)
    pub = Collector()
    await forecast_job.run_forecast(cid, 3, publish=pub)
    chips = pub.of("chip")
    assert {(e["member"], e["predicted"]) for e in chips} == {("asha", False), ("ravi", True)}


def test_window_closes_at_the_due_date(cid):
    advance_clock(cid, 3)  # exactly the due time: collection, not forecasting
    assert forecast_job.due_rounds(cid) == []


async def test_signals_count_current_declines_and_inbox_replies(cid):
    inbox.add_message(cid, "tomas", "Sorry, can't pay until next week", lang="en")
    with session_scope() as db:
        c = db.exec(
            select(Contribution).where(
                Contribution.circle_id == cid,
                Contribution.round_n == 3,
                Contribution.member_handle == "tomas",
            )
        ).one()
        c.status, c.attempt = "DECLINED", 1
        db.add(c)
    member = next(m for m in state.members_of(cid) if m.handle == "tomas")
    sig = forecast_job.member_signals(cid, 3, member, as_of=state.load_circle(cid).virtual_now)
    assert sig.attempts_this_round == 1 and any("next week" in r for r in sig.replies)
    assert sig.late_rounds == []  # the current round is never "history"


async def test_job_never_raises(cid, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(forecast_job, "due_rounds", boom)
    assert await forecast_job.on_clock_advanced(cid) == []


# --- routes ------------------------------------------------------------------------------------------------


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_judge_advance_clock_runs_the_forecast(client):
    sess = client.get("/api/session").json()
    r = client.post("/api/judge/advance_clock", json={"circle_id": sess["circle_id"], "days": 0})
    assert r.status_code == 200
    body = r.json()
    assert [f["member"] for f in body["forecasts"] if f["flagged"]] == ["ravi"]
    board = client.get(f"/api/circles/{sess['circle_id']}").json()
    assert board["forecasts"][0]["member"] == "ravi"
    assert {c["member"] for c in board["contributions"] if c["round_n"] == 3 and c["predicted"]} == {"ravi"}
    # a second click is a no-op for the forecaster
    r2 = client.post("/api/judge/advance_clock", json={"circle_id": sess["circle_id"], "days": 1})
    assert r2.json()["forecasts"] == []


def test_judge_run_forecast_forces_a_round(client):
    sess = client.get("/api/session").json()
    r = client.post("/api/judge/run_forecast", json={"circle_id": sess["circle_id"]})
    assert r.status_code == 200 and r.json()["round_n"] == 3 and len(r.json()["forecasts"]) == 6
    r = client.post("/api/judge/run_forecast", json={"circle_id": sess["circle_id"], "round_n": 4})
    assert r.status_code == 200 and r.json()["round_n"] == 4
    assert (
        client.post(
            "/api/judge/run_forecast", json={"circle_id": sess["circle_id"], "round_n": 99}
        ).status_code
        == 404
    )
