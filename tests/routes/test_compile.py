"""POST /api/circles/compile, the freeze/confirm step and GET /charter (rules mode; no LLM key in tests)."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.agent import charter as cc
from app.engine import state, tasks
from app.routes import compile as compile_routes

# A start a month ahead, so a new circle can be created whenever the suite runs (PayPal needs a future start).
START = (date.today() + timedelta(days=30)).isoformat()


def _rules(start: str = START, name: str = "Corner Store Circle") -> str:
    return (
        f"Name: {name}\nMembers: Ana Lopez, Bo Chen, Cy Diaz\n"
        f"We each pay $25 every week starting {start}.\nPast circles: Ana 4 rounds, Bo 1 round\n"
        "Decisions need 2 of 3 votes."
    )


CUSTOM = _rules()


def test_compile_needs_a_session(app):
    with TestClient(app) as c:
        assert c.post("/api/circles/compile", json={"text": "x"}).status_code == 401


def test_compile_demo_text_matches_the_session_circle(client, session):
    demo = client.get("/api/circles/compile/demo?lang=hi").json()
    assert demo["lang"] == "hi" and "नाम: Temple Circle A" in demo["text"]
    r = client.post("/api/circles/compile", json={"text": demo["text"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["source"] == "parser" and body["mode"] == "rules"
    assert body["mode_label"] == "rules mode: parser" and body["lang"] == "hi"
    assert body["draft_id"].startswith("cd_") and body["matches_circle"] is True
    assert body["charter_sha256"] == state.load_circle(session["circle_id"]).charter_sha256
    assert body["charter"]["amount"] == "200.00" and body["charter"]["quorum"] == "4/6"
    assert set(body["explanations"]) == set(cc.CHARTER_FIELDS) and body["problems"] == []


def test_compile_counts_the_live_budget_only_when_needed(client, session, monkeypatch):
    seen = []
    monkeypatch.setattr(tasks, "live_allowed_for", lambda request: seen.append(request) or False)
    body = client.post("/api/circles/compile", json={"text": CUSTOM}).json()
    assert len(seen) == 1 and body["live_ai_allowed"] is False and body["mode"] == "rules"
    monkeypatch.setattr(cc, "replay_available", lambda text, lang=None: True)
    client.post("/api/circles/compile", json={"text": CUSTOM})
    assert len(seen) == 1  # a recorded answer replays without spending budget


def test_compile_validation(client, session):
    assert client.post("/api/circles/compile", json={"text": ""}).status_code == 422
    assert client.post("/api/circles/compile", json={"text": "x" * 4001}).status_code == 422
    body = client.post("/api/circles/compile", json={"text": "please make us a circle"}).json()
    assert body["source"] == "template" and body["mode_label"] == "rules mode: template"


def test_freeze_preview_and_confirm_the_running_charter(client, session):
    cid = session["circle_id"]
    r = client.post(f"/api/circles/{cid}/charter/freeze", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matches_circle"] is True and body["confirmed"] is False and body["approval"] is None
    assert body["charter_sha256"] == state.load_circle(cid).charter_sha256
    assert body["canonical_json"].startswith('{"amount":"200.00"')
    plan = body["billing_plan"]
    assert (
        plan["billing_cycles"][0]["total_cycles"] == 6
        and plan["billing_cycles"][0]["frequency"]["interval_unit"] == "MONTH"
    )
    assert plan["one_time_charges"]["total_amount"]["value"] == "0.00"
    assert body["summary"]["last_due_date"] == "2027-04-01" and body["fees"]["fee_policy"] == "net_pot"

    r = client.post(
        f"/api/circles/{cid}/charter/freeze", json={"confirm": True}, headers={"user-agent": "phone-test"}
    )
    body = r.json()
    assert body["confirmed"] is True and body["created_circle"] is False and body["circle_id"] == cid
    assert (
        body["approval"]["approved_by"] == "priya (organizer)"
        and body["approval"]["charter_sha256"] == body["charter_sha256"]
    )
    assert len(body["invites"]) == 6
    got = client.get(f"/api/circles/{cid}/charter").json()
    assert got["approval"]["charter_sha256"] == body["charter_sha256"] and got["summary"]["cycles"] == 6


def test_confirming_a_custom_draft_creates_a_circle_to_invite_into(client, session):
    cid = session["circle_id"]
    draft = client.post("/api/circles/compile", json={"text": CUSTOM}).json()
    assert draft["ok"] and draft["matches_circle"] is False
    assert draft["charter"]["cadence"] == "weekly" and draft["charter"]["rotation"] == ["ana", "bo", "cy"]

    preview = client.post(f"/api/circles/{cid}/charter/freeze", json={"draft_id": draft["draft_id"]}).json()
    assert preview["charter_sha256"] == draft["charter_sha256"] and preview["matches_circle"] is False
    assert preview["billing_plan"]["billing_cycles"][0]["frequency"]["interval_unit"] == "WEEK"

    body = client.post(
        f"/api/circles/{cid}/charter/freeze", json={"draft_id": draft["draft_id"], "confirm": True}
    ).json()
    new = body["circle_id"]
    assert body["created_circle"] is True and new != cid
    assert [i["handle"] for i in body["invites"]] == ["ana", "bo", "cy"]
    circle = state.load_circle(new)
    assert (
        circle.kind == "compiled"
        and circle.charter_sha256 == draft["charter_sha256"]
        and not circle.is_fixture_clone
    )
    assert len(state.members_of(new)) == 3 and state.get_round(new, 3).recipient_handle == "cy"
    assert client.get(f"/api/circles/{new}/invites").status_code == 200
    assert client.get(f"/api/circles/{new}/charter").json()["approval"]["draft_id"] == draft["draft_id"]
    # confirming again is idempotent; the session keeps its demo board
    again = client.post(
        f"/api/circles/{cid}/charter/freeze", json={"draft_id": draft["draft_id"], "confirm": True}
    ).json()
    assert again["circle_id"] == new and again["created_circle"] is False
    assert client.get("/api/session").json()["circle_id"] == cid
    # the new circle's invite card works through the join flow
    token = body["invites"][0]["token"]
    card = client.get(f"/api/join/{token}").json()
    assert card["circle"]["amount"] == "25.00" and card["circle"]["kind"] == "compiled"


def test_freeze_is_session_scoped(app, client, session):
    draft = client.post("/api/circles/compile", json={"text": CUSTOM}).json()
    with TestClient(app) as other:
        mine = other.get("/api/session").json()["circle_id"]
        assert other.post(f"/api/circles/{session['circle_id']}/charter/freeze", json={}).status_code == 403
        r = other.post(f"/api/circles/{mine}/charter/freeze", json={"draft_id": draft["draft_id"]})
        assert r.status_code == 404
        assert other.get(f"/api/circles/{session['circle_id']}/charter").status_code == 403


def _confirm(client, cid, text):
    draft = client.post("/api/circles/compile", json={"text": text}).json()
    assert draft["ok"], draft
    return client.post(
        f"/api/circles/{cid}/charter/freeze", json={"draft_id": draft["draft_id"], "confirm": True}
    )


def test_a_new_circle_gets_a_future_start_date(client, session):
    """Review: a past start_date reached PayPal's billing_plan untested (likely a 422, shown as a generic 502)."""
    from app.engine.common import utcnow

    cid = session["circle_id"]
    draft = client.post("/api/circles/compile", json={"text": _rules("2025-01-01")}).json()
    preview = client.post(f"/api/circles/{cid}/charter/freeze", json={"draft_id": draft["draft_id"]}).json()
    moved = cc._next_month_start(utcnow().date())
    assert preview["adjustments"] and "not in the future" in preview["adjustments"][0]
    assert preview["charter_sha256"] != draft["charter_sha256"]  # the organizer sees the moved charter's hash
    assert preview["billing_plan"]["billing_cycles"][0]["start_date"] == moved.isoformat()
    body = client.post(
        f"/api/circles/{cid}/charter/freeze", json={"draft_id": draft["draft_id"], "confirm": True}
    ).json()
    assert body["created_circle"] is True and body["charter_sha256"] == preview["charter_sha256"]
    new = state.load_circle(body["circle_id"])
    assert new.charter_sha256 == preview["charter_sha256"] and state.charter_of(new).start_date == moved

    far = (date.today() + timedelta(days=400)).isoformat()
    r = _confirm(client, cid, _rules(far, name="Far Circle"))
    assert r.status_code == 422 and "more than a year" in r.json()["detail"]["message"]
    assert _compiled_count() == 1
    # the running demo charter is approved in place (no adjustment), whatever its start date
    r = client.post(f"/api/circles/{cid}/charter/freeze", json={"confirm": True})
    assert r.status_code == 200 and r.json()["adjustments"] == []


def _compiled_count() -> int:
    from sqlmodel import select

    from app.db import session_scope
    from app.models import Circle

    with session_scope() as db:
        return len(list(db.exec(select(Circle.id).where(Circle.kind == "compiled")).all()))


def test_compiled_circles_and_drafts_are_capped_per_session(client, session, monkeypatch):
    from sqlmodel import func, select

    from app.db import session_scope
    from app.models import CharterDraftRow

    cid = session["circle_id"]
    for i in range(compile_routes.MAX_COMPILED_CIRCLES_PER_SESSION):
        assert _confirm(client, cid, _rules(name=f"Circle {i}")).status_code == 200
    r = _confirm(client, cid, _rules(name="One too many"))
    assert r.status_code == 429 and "reset the demo" in r.json()["detail"]

    monkeypatch.setattr(compile_routes, "MAX_DRAFTS_PER_SESSION", 3)
    for _ in range(5):
        client.post("/api/circles/compile", json={"text": CUSTOM})
    with session_scope() as db:
        assert db.exec(select(func.count()).select_from(CharterDraftRow)).one() == 3
