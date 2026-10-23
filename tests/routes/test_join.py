"""Member join flow: invite tokens, the pre-consent card, setup/return against a fake vault wrapper."""

from types import SimpleNamespace

from app.engine import state
from app.routes import join


def test_invites_need_the_owning_session(client, session):
    assert client.get("/api/circles/zzzzzz/invites").status_code == 403
    r = client.get(f"/api/circles/{session['circle_id']}/invites")
    assert r.status_code == 200
    invites = r.json()
    assert [i["handle"] for i in invites] == ["asha", "ravi", "meena", "tomas", "lin", "dev"]
    assert all(i["vaulted"] is False and i["url"].endswith(f"/join/{i['token']}") for i in invites)
    assert all(set(i) == {"handle", "name", "token", "url", "vaulted"} for i in invites)


def test_invites_without_session_is_401(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        assert c.get("/api/circles/abcdef/invites").status_code == 401


def test_join_card_and_tampered_token(client, session):
    token = join.invite_token(session["circle_id"], "ravi")
    r = client.get(f"/api/join/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["member"] == {"handle": "ravi", "name": "Ravi Sharma", "lang": "hi"}
    card = body["circle"]
    assert card["amount"] == "200.00" and card["cycles"] == 6 and card["quorum"] == "4/6"
    assert card["charter_sha256"] == state.load_circle(session["circle_id"]).charter_sha256
    assert body["vaulted"] is False

    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert client.get(f"/api/join/{tampered}").status_code == 404
    assert client.get("/api/join/garbage").status_code == 404
    other = join.invite_token(session["circle_id"], "nobody")
    assert client.get(f"/api/join/{other}").status_code == 404


def test_setup_and_return_store_the_vault_id(client, session, monkeypatch):
    calls: dict = {}

    async def create_setup_token(gw, **kw):
        calls["setup"] = kw
        return {
            "id": "FAKESETUP1",
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/approve"}],
        }

    async def create_payment_token(gw, *, setup_token_id, request_id):
        calls["payment"] = {"setup_token_id": setup_token_id, "request_id": request_id}
        return {"id": "FAKEVAULTRAVI", "customer": {"id": "FAKECUSTRAVI"}}

    vault = SimpleNamespace(
        create_setup_token=create_setup_token,
        create_payment_token=create_payment_token,
        approve_link=lambda data: data["links"][0]["href"],
    )
    from app.paypal import keys

    monkeypatch.setattr(join, "_paypal_modules", lambda: (vault, object(), keys))
    token = join.invite_token(session["circle_id"], "ravi")

    r = client.post(f"/api/join/{token}/setup")
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "setup_token_id": "FAKESETUP1",
        "approve_url": "https://www.sandbox.paypal.com/approve",
    }
    assert calls["setup"]["request_id"] == f"tk:{session['run_id']}:{session['circle_id']}:setup:ravi"
    assert calls["setup"]["billing_plan"]["billing_cycles"][0]["total_cycles"] == 6
    assert calls["setup"]["return_url"].endswith(f"/join/{token}/return")

    r = client.get(f"/join/{token}/return", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?joined=ravi"
    assert calls["payment"]["setup_token_id"] == "FAKESETUP1"
    member = next(m for m in state.members_of(session["circle_id"]) if m.handle == "ravi")
    assert member.vault_id == "FAKEVAULTRAVI" and member.customer_id == "FAKECUSTRAVI"
    assert member.setup_token_id is None
    board = client.get(f"/api/circles/{session['circle_id']}").json()
    assert next(m for m in board["members"] if m["handle"] == "ravi")["vaulted"] is True

    r = client.get(f"/join/{token}/cancel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?join=cancelled&member=ravi"


def test_return_without_setup_token_redirects_to_error(client, session, monkeypatch):
    monkeypatch.setattr(join, "_paypal_modules", lambda: (None, None, None))
    token = join.invite_token(session["circle_id"], "asha")
    r = client.get(f"/join/{token}/return", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?join=error&member=asha"
