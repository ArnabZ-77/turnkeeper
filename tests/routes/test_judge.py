"""POST /api/judge/{action}."""

from app.engine.events import get_event_bus
from routes.conftest import wait_for_task


def test_judge_needs_a_session(client):
    assert client.post("/api/judge/reset", json={"circle_id": "abcdef"}).status_code == 401


def test_unknown_action_is_404(client, session):
    r = client.post("/api/judge/explode", json={"circle_id": session["circle_id"]})
    assert r.status_code == 404


def test_other_circle_is_403(client, session):
    r = client.post("/api/judge/advance_clock", json={"circle_id": "zzzzzz", "days": 1})
    assert r.status_code == 403


def test_reset_returns_a_new_session_shape(client, session):
    r = client.post("/api/judge/reset", json={"circle_id": session["circle_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["action"] == "reset"
    assert body["session"]["circle_id"] != session["circle_id"] and "sid" not in body["session"]
    types = [e["type"] for e in get_event_bus().recent(session["sid"])]
    assert "toast" in types and types[-1] == "board"


def test_advance_clock(client, session):
    before = client.get(f"/api/circles/{session['circle_id']}").json()["circle"]["virtual_now"]
    r = client.post("/api/judge/advance_clock", json={"circle_id": session["circle_id"], "days": 3})
    assert r.status_code == 200 and r.json()["ok"] is True
    after = r.json()["virtual_now"]
    assert after > before and after.endswith("Z")
    assert client.get(f"/api/circles/{session['circle_id']}").json()["circle"]["virtual_now"] == after
    bad = client.post("/api/judge/advance_clock", json={"circle_id": session["circle_id"], "days": -1})
    assert bad.status_code == 422


def test_toggle_flags(client, session):
    cid = session["circle_id"]
    r = client.post("/api/judge/toggle_ravi_declines", json={"circle_id": cid})
    assert r.status_code == 200 and r.json()["flags"]["ravi_declines"] is False  # fixture starts on
    r = client.post("/api/judge/toggle_ravi_declines", json={"circle_id": cid, "on": True})
    assert r.json()["flags"]["ravi_declines"] is True
    r = client.post("/api/judge/make_next_payout_unclaimed", json={"circle_id": cid, "on": True})
    assert r.json()["flags"] == {"ravi_declines": True, "next_payout_unclaimed": True}
    board = client.get(f"/api/circles/{cid}").json()
    assert board["circle"]["flags"]["next_payout_unclaimed"] is True


def test_run_round_requires_round_n_and_starts_a_task(client, vaulted, paypal):
    cid = vaulted["circle_id"]
    assert client.post("/api/judge/run_round", json={"circle_id": cid}).status_code == 422
    r = client.post("/api/judge/run_round", json={"circle_id": cid, "round_n": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["kind"] == "collect_round" and body["task_id"]
    done = wait_for_task(client, body["task_id"])
    assert done["done"] is True and done["kind"] == "collect_round"
    row = {
        c["member"]: c for c in client.get(f"/api/circles/{cid}").json()["contributions"] if c["round_n"] == 3
    }
    assert row["ravi"]["status"] == "DECLINED" and row["asha"]["status"] == "COMPLETED"
