"""GET /api/session, POST /api/session/reset, the cookie, the cap and GET /api/circles/{id}."""

from fastapi.testclient import TestClient

from app.engine.sessions import COOKIE_NAME

SESSION_KEYS = {"sid", "run_id", "circle_id", "live_ai", "paypal_live", "frozen"}


def test_session_shape_and_cookie(client):
    r = client.get("/api/session")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == SESSION_KEYS
    assert body["live_ai"] is False and body["paypal_live"] is True and body["frozen"] is False
    assert len(body["circle_id"]) == 6 and len(body["run_id"]) == 8
    cookie = r.headers["set-cookie"]
    assert (
        cookie.startswith(f"{COOKIE_NAME}=")
        and "HttpOnly" in cookie
        and "SameSite=lax" in cookie.replace("samesite=lax", "SameSite=lax")
    )
    again = client.get("/api/session").json()
    assert again == body  # same cookie -> same session


def test_two_cookies_two_circles_two_run_ids(app):
    with TestClient(app) as a, TestClient(app) as b:
        sa = a.get("/api/session").json()
        sb = b.get("/api/session").json()
    assert sa["sid"] != sb["sid"]
    assert sa["circle_id"] != sb["circle_id"]
    assert sa["run_id"] != sb["run_id"]
    with TestClient(app) as c:
        assert c.get(f"/api/circles/{sa['circle_id']}").json()["circle"]["run_id"] == sa["run_id"]
        assert c.get(f"/api/circles/{sb['circle_id']}").json()["circle"]["run_id"] == sb["run_id"]


def test_reset_gives_new_clone_same_cookie(client, session):
    r = client.post("/api/session/reset")
    assert r.status_code == 200
    new = r.json()
    assert set(new) == SESSION_KEYS and new["sid"] == session["sid"]
    assert new["circle_id"] != session["circle_id"] and new["run_id"] != session["run_id"]
    assert client.get(f"/api/circles/{session['circle_id']}").json()["circle"]["status"] == "closed"
    assert client.get("/api/session").json()["circle_id"] == new["circle_id"]


def test_reset_without_session_is_401(client):
    assert client.post("/api/session/reset").status_code == 401


def test_waitlist_when_cap_reached(app, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TK_MAX_ACTIVE_SESSIONS", "1")
    get_settings.cache_clear()
    with TestClient(app) as a, TestClient(app) as b:
        assert a.get("/api/session").status_code == 200
        r = b.get("/api/session")
    assert r.status_code == 429
    assert r.json()["waitlist"] is True and r.headers["retry-after"] == "120"


def test_board_route(client, session):
    r = client.get(f"/api/circles/{session['circle_id']}")
    assert r.status_code == 200
    board = r.json()
    assert set(board) == {"circle", "members", "rounds", "contributions", "counters", "ticker"}
    assert board["circle"]["id"] == session["circle_id"] and board["circle"]["amount"] == "200.00"
    assert len(board["members"]) == 6 and len(board["rounds"]) == 6 and len(board["contributions"]) == 36
    assert client.get("/api/circles/zzzzzz").status_code == 404


def test_healthz_still_boots(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["env"] == "sandbox"
