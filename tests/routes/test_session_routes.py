"""GET /api/session, POST /api/session/reset, the cookie, the cap and GET /api/circles/{id}."""

from fastapi.testclient import TestClient

from app.engine.sessions import COOKIE_NAME

SESSION_KEYS = {"sid", "run_id", "circle_id", "live_ai", "paypal_live", "frozen", "vault_seeded"}


def test_session_shape_and_cookie(client):
    r = client.get("/api/session")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == SESSION_KEYS
    assert body["live_ai"] is False and body["paypal_live"] is True and body["frozen"] is False
    assert body["vault_seeded"] is False  # fixture placeholders, no TK_SEED_VAULT_IDS in tests
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
        assert a.get(f"/api/circles/{sa['circle_id']}").json()["circle"]["run_id"] == sa["run_id"]
        assert b.get(f"/api/circles/{sb['circle_id']}").json()["circle"]["run_id"] == sb["run_id"]
        # M1 review: a session never reads another session's circle.
        assert a.get(f"/api/circles/{sb['circle_id']}").status_code == 403
        assert b.get(f"/api/circles/{sa['circle_id']}").status_code == 403
    with TestClient(app) as c:  # no cookie at all
        assert c.get(f"/api/circles/{sa['circle_id']}").status_code == 401


def test_reset_gives_new_clone_same_cookie(client, session):
    r = client.post("/api/session/reset")
    assert r.status_code == 200
    new = r.json()
    assert set(new) == SESSION_KEYS and new["sid"] == session["sid"]
    assert new["circle_id"] != session["circle_id"] and new["run_id"] != session["run_id"]
    from app.engine import state

    assert state.load_circle(session["circle_id"]).status == "closed"
    assert client.get(f"/api/circles/{session['circle_id']}").status_code == 403  # no longer this session's
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
    assert set(board) == {"circle", "members", "rounds", "contributions", "counters", "ticker", "recovery"}
    assert board["circle"]["id"] == session["circle_id"] and board["circle"]["amount"] == "200.00"
    assert len(board["members"]) == 6 and len(board["rounds"]) == 6 and len(board["contributions"]) == 36
    assert client.get("/api/circles/zzzzzz").status_code == 403  # not this session's circle


def test_healthz_still_boots(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["env"] == "sandbox"


def test_session_reports_vault_seeded_once_members_are_vaulted(client, session):
    from fakes import vault_members

    vault_members(session["circle_id"])
    assert client.get("/api/session").json()["vault_seeded"] is True


def test_cookie_is_secure_behind_an_https_proxy_even_with_default_base_url(app):
    """M1 review: Secure follows the browser's scheme (X-Forwarded-Proto), not only PUBLIC_BASE_URL."""
    with TestClient(app) as c:
        r = c.get("/api/session", headers={"X-Forwarded-Proto": "https"})
        assert "secure" in r.headers["set-cookie"].lower()
    with TestClient(app) as c:
        assert "secure" not in c.get("/api/session").headers["set-cookie"].lower()


def test_healthz_reports_vault_seeding_and_config_warnings(client, monkeypatch):
    from app.config import get_settings

    body = client.get("/healthz").json()
    assert body["vault_seeded"] is False
    assert any("TK_SEED_VAULT_IDS" in w for w in body["warnings"])
    monkeypatch.setenv(
        "TK_SEED_VAULT_IDS",
        '{"asha":"V-A","ravi":"V-R","meena":"V-M","tomas":"V-T","lin":"V-L","dev":"V-D"}',
    )
    get_settings.cache_clear()
    body = client.get("/healthz").json()
    assert body["vault_seeded"] is True and not any("TK_SEED_VAULT_IDS" in w for w in body["warnings"])
    # served on a public host while PUBLIC_BASE_URL is still the localhost default
    r = client.get("/healthz", headers={"host": "turnkeeper.onrender.com"})
    assert any("PUBLIC_BASE_URL" in w for w in r.json()["warnings"])
