"""Member join flow: expiring invites, the pre-consent card, start/return/cancel/wallet against a fake vault
wrapper (never the network), PayPal error paths and the judge mini-circle."""

from datetime import timedelta

import pytest

from app.engine import join as jn
from app.engine import state
from app.engine.common import utcnow
from app.paypal import keys
from app.paypal.client import PayPalError
from app.routes import join


class FakeVault:
    """Stands in for app.paypal.vault; records calls. Configure statuses/errors per test."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.n = 0
        self.setup_status = "APPROVED"
        self.setup_error: Exception | None = None
        self.payment_error: Exception | None = None
        self.email: str | None = "judge.member@example.com"

    async def create_setup_token(self, gw, **kw):
        self.calls.append(("setup", kw))
        if self.setup_error:
            raise self.setup_error
        self.n += 1
        return {
            "id": f"FAKESETUP{self.n}",
            "status": "PAYER_ACTION_REQUIRED",
            "links": [{"rel": "approve", "href": f"https://www.sandbox.paypal.com/approve/{self.n}"}],
        }

    async def get_setup_token(self, gw, *, setup_token_id):
        self.calls.append(("get_setup", {"setup_token_id": setup_token_id}))
        return {"id": setup_token_id, "status": self.setup_status}

    async def create_payment_token(self, gw, *, setup_token_id, request_id):
        self.calls.append(("payment", {"setup_token_id": setup_token_id, "request_id": request_id}))
        if self.payment_error:
            raise self.payment_error
        data = {"id": "FAKEVAULT1", "customer": {"id": "FAKECUST1"}}
        if self.email:
            data["payment_source"] = {"paypal": {"email_address": self.email}}
        return data

    @staticmethod
    def approve_link(data):
        return next(
            (lnk["href"] for lnk in data.get("links", []) if lnk["rel"] in ("approve", "payer-action")), None
        )

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]


@pytest.fixture
def vault(monkeypatch) -> FakeVault:
    fake = FakeVault()
    monkeypatch.setattr(join, "_paypal_modules", lambda: (fake, object(), keys))
    return fake


def _member(cid, handle):
    return next(m for m in state.members_of(cid) if m.handle == handle)


def _token_of(location: str) -> str:
    return location.split("/join/", 1)[1].split("?", 1)[0]


def _decline(code: str, name: str = "UNPROCESSABLE_ENTITY") -> PayPalError:
    return PayPalError(422, {"name": name, "message": "declined", "details": [{"issue": code}]})


# --- invites ---------------------------------------------------------------------------------------------


def test_invites_need_the_owning_session(client, session):
    assert client.get("/api/circles/zzzzzz/invites").status_code == 403
    r = client.get(f"/api/circles/{session['circle_id']}/invites")
    assert r.status_code == 200
    invites = r.json()
    assert [i["handle"] for i in invites] == ["asha", "ravi", "meena", "tomas", "lin", "dev"]
    assert all(i["vaulted"] is False and i["url"].endswith(f"/join/{i['token']}") for i in invites)
    assert all(i["join_status"] == "invited" and i["expires_at"] for i in invites)
    assert all(i["url"].startswith("http://localhost:8000/join/") for i in invites)


def test_invites_without_session_is_401(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        assert c.get("/api/circles/abcdef/invites").status_code == 401


def test_invite_token_signature_and_expiry():
    now = utcnow()
    token = jn.make_invite("abc123", "ravi", now=now)
    inv = jn.parse_invite(token, now=now)
    assert (inv.circle_id, inv.handle) == ("abc123", "ravi")
    assert inv.expires_at > now + timedelta(days=6)
    with pytest.raises(jn.InviteError) as e:
        jn.parse_invite(token, now=now + timedelta(days=8))
    assert e.value.kind == "expired"
    assert jn.parse_invite(token, now=now + timedelta(days=8), allow_expired=True).handle == "ravi"
    cid, handle, exp, sig = token.split(".")
    for bad in (
        f"{cid}.dev.{exp}.{sig}",  # another member
        f"{cid}.{handle}.{exp}z.{sig}",  # extended expiry
        f"{cid}.{handle}.{sig}",  # M1 3-part format
        "garbage",
    ):
        with pytest.raises(jn.InviteError) as e:
            jn.parse_invite(bad, now=now)
        assert e.value.kind == "invalid"


def test_invite_needs_the_session_secret(monkeypatch):
    from app.config import get_settings

    token = jn.make_invite("abc123", "ravi")
    monkeypatch.setenv("SESSION_SECRET", "another-secret-of-enough-length")
    get_settings.cache_clear()
    with pytest.raises(jn.InviteError):
        jn.parse_invite(token)


# --- the pre-consent card ---------------------------------------------------------------------------------


def test_join_card_and_bad_tokens(client, session):
    cid = session["circle_id"]
    token = join.invite_token(cid, "ravi")
    r = client.get(f"/api/join/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["member"] == {"handle": "ravi", "name": "Ravi Sharma", "lang": "hi"}
    card = body["circle"]
    assert card["amount"] == "200.00" and card["cycles"] == 6 and card["quorum"] == "4/6"
    assert card["charter_sha256"] == state.load_circle(cid).charter_sha256
    assert card["fees"]["fee_estimate"] == "7.47" and card["fees"]["net_per_contribution"] == "192.53"
    assert card["fees"]["pot_estimate"] == "1154.93"  # 6 x 192.53 - 0.25 payout fee
    assert "$7.47" in card["fee_note"] and "net of fees" in card["fee_note"]
    plan = card["billing_plan"]["billing_cycles"][0]
    assert plan["total_cycles"] == 6 and plan["pricing_scheme"]["pricing_model"] == "VARIABLE"
    assert body["join"]["payout_round"] == 5 and body["join"]["join_status"] == "invited"
    assert body["join"]["payout_date"] == "2027-03-01"
    assert body["vaulted"] is False and body["paypal_ready"] is True
    assert "once" in body["consent"]

    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert client.get(f"/api/join/{tampered}").status_code == 404
    assert client.get("/api/join/garbage").status_code == 404
    assert client.get(f"/api/join/{join.invite_token(cid, 'nobody')}").status_code == 404
    expired = jn.make_invite(cid, "ravi", now=utcnow() - timedelta(days=30))
    r = client.get(f"/api/join/{expired}")
    assert r.status_code == 410 and "expired" in r.json()["detail"]


# --- start -> PayPal -> return ----------------------------------------------------------------------------


def test_start_and_return_store_the_vault_id(client, session, vault):
    cid, run = session["circle_id"], session["run_id"]
    token = join.invite_token(cid, "ravi")

    r = client.post(f"/api/join/{token}/start")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "ok": True,
        "setup_token_id": "FAKESETUP1",
        "approve_url": "https://www.sandbox.paypal.com/approve/1",
        "status": "pending_approval",
    }
    kw = vault.calls[0][1]
    assert kw["request_id"] == f"tk:{run}:{cid}:setup:ravi"
    assert kw["billing_plan"]["billing_cycles"][0]["total_cycles"] == 6
    assert kw["billing_plan"]["one_time_charges"]["total_amount"]["value"] == "0.00"
    assert kw["return_url"] == f"http://localhost:8000/join/{token}/return"
    assert kw["cancel_url"] == f"http://localhost:8000/join/{token}/cancel"
    assert kw["description"] == "Turnkeeper · Temple Circle A · $200.00 monthly × 6"
    assert client.get(f"/api/join/{token}").json()["join"]["join_status"] == "pending_approval"

    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?status=vaulted")
    fresh = _token_of(r.headers["location"])
    assert fresh != token  # the invite nonce rotated: the member continues on a fresh link
    assert vault.kinds() == ["setup", "get_setup", "payment"]
    assert vault.calls[2][1] == {
        "setup_token_id": "FAKESETUP1",
        "request_id": f"tk:{run}:{cid}:setup:ravi:pt",
    }
    member = _member(cid, "ravi")
    assert member.vault_id == "FAKEVAULT1" and member.customer_id == "FAKECUST1"
    assert member.setup_token_id is None and member.join_status == "vaulted" and member.joined_at
    assert member.payout_receiver == "judge.member@example.com"  # the approving account is the default payee
    card = client.get(f"/api/join/{fresh}").json()
    assert card["vaulted"] is True and card["join"]["payout_receiver_masked"] == "j***@example.com"
    board = client.get(f"/api/circles/{cid}").json()
    assert next(m for m in board["members"] if m["handle"] == "ravi")["vaulted"] is True

    # the pre-join link is revoked; a second start on the fresh link is harmless
    assert client.get(f"/api/join/{token}").status_code == 404
    # Back / reload on the old return URL lands on the SPA join page (not a raw JSON 404) and saves nothing
    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/join/{token}?status=invalid"
    assert vault.kinds().count("payment") == 1
    r = client.get(f"/join/{token}/cancel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/join/{token}?status=invalid"
    assert jn.is_vaulted(_member(cid, "ravi"))
    r = client.post(f"/api/join/{fresh}/start")
    assert r.status_code == 409 and r.json()["detail"]["status"] == "vaulted"


def test_setup_alias_still_works(client, session, vault):
    token = join.invite_token(session["circle_id"], "asha")
    r = client.post(f"/api/join/{token}/setup")
    assert r.status_code == 200 and r.json()["approve_url"].endswith("/approve/1")


def test_cancel_then_retry_uses_a_new_request_id(client, session, vault):
    cid, run = session["circle_id"], session["run_id"]
    token = join.invite_token(cid, "lin")
    assert client.post(f"/api/join/{token}/start").status_code == 200
    r = client.get(f"/join/{token}/cancel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/join/{token}?status=cancelled"
    card = client.get(f"/api/join/{token}").json()["join"]
    assert card["join_status"] == "cancelled" and "Nothing was saved" in card["join_message"]
    assert _member(cid, "lin").setup_token_id is None
    # a stale return after cancelling does not vault anything
    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.headers["location"].endswith("status=error") and "payment" not in vault.kinds()

    assert client.post(f"/api/join/{token}/start").json()["setup_token_id"] == "FAKESETUP2"
    assert vault.calls[-1][1]["request_id"] == f"tk:{run}:{cid}:setup:lin:a2"
    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP2", follow_redirects=False)
    assert r.headers["location"].endswith("status=vaulted")
    assert vault.calls[-1][1]["request_id"] == f"tk:{run}:{cid}:setup:lin:a2:pt"


def test_return_when_paypal_still_needs_the_payer(client, session, vault):
    cid = session["circle_id"]
    token = join.invite_token(cid, "dev")
    client.post(f"/api/join/{token}/start")
    vault.setup_status = "PAYER_ACTION_REQUIRED"
    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.headers["location"] == f"/join/{token}?status=action_required"
    assert "payment" not in vault.kinds()
    m = _member(cid, "dev")
    assert m.join_status == "action_required" and "Continue to PayPal" in m.join_message
    assert m.setup_token_id == "FAKESETUP1"  # still pending; the member can approve and come back
    vault.setup_status = "APPROVED"
    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.headers["location"].endswith("status=vaulted")


@pytest.mark.parametrize(
    ("error", "status", "phrase"),
    [
        (_decline("INSTRUMENT_DECLINED"), "declined", "INSTRUMENT_DECLINED"),
        (_decline("PAYER_ACTION_REQUIRED"), "action_required", "finish approving"),
        (_decline("SETUP_TOKEN_NOT_APPROVED"), "action_required", "finish approving"),
        (PayPalError(500, {"name": "INTERNAL_SERVER_ERROR"}), "error", "INTERNAL_SERVER_ERROR"),
    ],
)
def test_return_maps_payment_token_errors(client, session, vault, error, status, phrase):
    cid = session["circle_id"]
    token = join.invite_token(cid, "meena")
    client.post(f"/api/join/{token}/start")
    vault.payment_error = error
    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.headers["location"] == f"/join/{token}?status={status}"
    m = _member(cid, "meena")
    assert m.join_status == status and phrase in m.join_message
    assert not jn.is_vaulted(m)
    assert client.get(f"/api/join/{token}").json()["join"]["join_message"] == m.join_message


def test_return_with_a_foreign_approval_token_is_refused(client, session, vault):
    cid = session["circle_id"]
    token = join.invite_token(cid, "tomas")
    client.post(f"/api/join/{token}/start")
    r = client.get(f"/join/{token}/return?approval_token_id=SOMEONE_ELSES", follow_redirects=False)
    assert r.headers["location"].endswith("status=error")
    assert vault.kinds() == ["setup"] and not jn.is_vaulted(_member(cid, "tomas"))


def test_return_without_setup_token_redirects_to_error(client, session, vault):
    token = join.invite_token(session["circle_id"], "asha")
    r = client.get(f"/join/{token}/return", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/join/{token}?status=error"
    assert vault.calls == []
    r = client.get("/join/garbage/return", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/join/garbage?status=invalid"


def test_return_without_approval_token_id_is_not_paypals_redirect(client, session, vault):
    cid = session["circle_id"]
    token = join.invite_token(cid, "asha")
    assert client.post(f"/api/join/{token}/start").status_code == 200
    r = client.get(f"/join/{token}/return", follow_redirects=False)
    assert r.headers["location"] == f"/join/{token}?status=error"
    assert vault.kinds() == ["setup"] and not jn.is_vaulted(_member(cid, "asha"))
    assert "does not belong" in _member(cid, "asha").join_message


def test_invite_nonce_lookup_failure_fails_closed(client, session, vault, monkeypatch):
    """A DB error during the nonce lookup must not fall back to a nonce-less check (that would re-accept
    links revoked when the member vaulted)."""
    cid = session["circle_id"]
    token = join.invite_token(cid, "dev")

    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(jn, "_invite_nonce", boom)
    with pytest.raises(jn.InviteError) as e:
        jn.parse_invite(token)
    assert e.value.kind == "unavailable"
    assert client.get(f"/api/join/{token}").status_code == 503
    assert client.post(f"/api/join/{token}/wallet", json={"receiver": "m@example.com"}).status_code == 503
    r = client.get(f"/join/{token}/return?approval_token_id=X", follow_redirects=False)
    assert r.headers["location"] == f"/join/{token}?status=error" and vault.calls == []


def test_start_paypal_error_is_502_with_a_message(client, session, vault):
    vault.setup_error = PayPalError(
        422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INCOMPATIBLE_PARAMETER_VALUE"}]}
    )
    token = join.invite_token(session["circle_id"], "asha")
    r = client.post(f"/api/join/{token}/start")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["issues"] == ["INCOMPATIBLE_PARAMETER_VALUE"] and detail["status"] == "error"
    assert "Nothing was saved" in detail["message"]


def test_start_without_sandbox_credentials_is_503(client, session, vault, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    get_settings.cache_clear()
    token = join.invite_token(session["circle_id"], "asha")
    r = client.post(f"/api/join/{token}/start")
    assert r.status_code == 503 and vault.calls == []
    assert client.get(f"/api/join/{token}").json()["paypal_ready"] is False


def test_expired_invite_cannot_start_but_can_finish(client, session, vault):
    cid = session["circle_id"]
    old = jn.make_invite(cid, "asha", now=utcnow() - timedelta(days=30))
    assert client.post(f"/api/join/{old}/start").status_code == 410
    fresh = join.invite_token(cid, "asha")
    client.post(f"/api/join/{fresh}/start")
    r = client.get(f"/join/{old}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.headers["location"].endswith("?status=vaulted")
    fresh_after = _token_of(r.headers["location"])
    assert jn.parse_invite(fresh_after).handle == "asha"  # a fresh, unexpired link


# --- payout wallet ----------------------------------------------------------------------------------------


def _vault_asha(cid, *, receiver=None, joined_ago=timedelta(minutes=1)):
    jn.update_member(
        cid,
        "asha",
        vault_id="FAKEVAULTASHA",
        join_status=jn.VAULTED,
        joined_at=utcnow() - joined_ago,
        payout_receiver=receiver,
    )


def test_wallet_choice(client, session):
    cid = session["circle_id"]
    _vault_asha(cid)
    token = join.invite_token(cid, "asha")
    r = client.post(f"/api/join/{token}/wallet", json={"wallet": "VENMO", "receiver": "+15550001111"})
    assert r.status_code == 422 and "Venmo" in r.json()["detail"]
    assert client.post(f"/api/join/{token}/wallet", json={"wallet": "PAYPAL"}).status_code == 422
    assert client.post(f"/api/join/{token}/wallet", json={"receiver": "not-an-email"}).status_code == 422
    r = client.post(f"/api/join/{token}/wallet", json={"wallet": "PAYPAL", "receiver": "asha.p@example.com"})
    assert r.status_code == 200 and r.json() == {
        "ok": True,
        "payout_wallet": "PAYPAL",
        "payout_receiver_masked": "a***@example.com",
    }
    assert _member(cid, "asha").payout_receiver == "asha.p@example.com"


def test_wallet_needs_a_vaulted_member(client, session):
    token = join.invite_token(session["circle_id"], "dev")
    r = client.post(f"/api/join/{token}/wallet", json={"receiver": "mallory@example.com"})
    assert r.status_code == 409 and "Join first" in r.json()["detail"]


def test_wallet_change_after_the_window_needs_the_organizer(client, session):
    """Fixer: an old shared link must not redirect the pot after joining (the mandate binds handles only)."""
    from sqlmodel import select

    from app.db import session_scope
    from app.models import AgentTrace

    cid = session["circle_id"]
    _vault_asha(cid, receiver="asha.real@example.com", joined_ago=timedelta(days=7))
    token = join.invite_token(cid, "asha")
    r = client.post(f"/api/join/{token}/wallet", json={"receiver": "mallory@example.com"})
    assert r.status_code == 409 and "ask the organizer" in r.json()["detail"]
    assert _member(cid, "asha").payout_receiver == "asha.real@example.com"
    # confirming the receiver on file is not a change
    r = client.post(f"/api/join/{token}/wallet", json={"wallet": "PAYPAL"})
    assert r.status_code == 200 and r.json()["payout_receiver_masked"] == "a***@example.com"

    # within the window a change is allowed, logged and announced to the organizer
    _vault_asha(cid, receiver="asha.real@example.com", joined_ago=timedelta(minutes=5))
    r = client.post(f"/api/join/{token}/wallet", json={"receiver": "asha.other@example.com"})
    assert r.status_code == 200
    assert _member(cid, "asha").payout_receiver == "asha.other@example.com"
    with session_scope() as db:
        logged = [t.payload for t in db.exec(select(AgentTrace).where(AgentTrace.circle_id == cid)).all()]
    assert any("asha changed the payout receiver" in p and "@" in p and "asha.other" not in p for p in logged)


def test_vaulting_revokes_links_handed_out_before(client, session, vault):
    cid = session["circle_id"]
    shared = join.invite_token(cid, "dev")  # e.g. posted in a group chat
    own = join.invite_token(cid, "dev")
    assert client.post(f"/api/join/{own}/start").status_code == 200
    r = client.get(f"/join/{own}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    fresh = _token_of(r.headers["location"])
    assert _member(cid, "dev").invite_nonce
    r = client.post(f"/api/join/{shared}/wallet", json={"receiver": "mallory@example.com"})
    assert r.status_code == 404
    assert client.get(f"/api/join/{fresh}").status_code == 200
    # organizer-minted links after joining carry the new nonce
    listed = {i["handle"]: i["token"] for i in client.get(f"/api/circles/{cid}/invites").json()}
    assert client.get(f"/api/join/{listed['dev']}").status_code == 200


def test_start_is_capped_per_invite_and_per_ip(client, session, vault):
    cid = session["circle_id"]
    token = join.invite_token(cid, "lin")
    for _ in range(jn.MAX_SETUP_ATTEMPTS):
        assert client.post(f"/api/join/{token}/start").status_code == 200
    r = client.post(f"/api/join/{token}/start")
    assert r.status_code == 429 and "ask the organizer" in r.json()["detail"]
    assert vault.kinds().count("setup") == jn.MAX_SETUP_ATTEMPTS

    # per IP: 10 setup tokens an hour across invites
    for handle in ("asha", "meena"):
        t = join.invite_token(cid, handle)
        for _ in range(3 if handle == "asha" else 2):
            assert client.post(f"/api/join/{t}/start").status_code == 200
    t = join.invite_token(cid, "tomas")
    r = client.post(f"/api/join/{t}/start")
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert vault.kinds().count("setup") == join.SETUP_TOKENS_PER_IP_HOUR


# --- judge mini-circle --------------------------------------------------------------------------------------


def test_judge_mini_circle_uses_the_same_flow(client, session, vault):
    r = client.post("/api/join/judge-circle")
    assert r.status_code == 200, r.text
    body = r.json()
    mini = body["circle_id"]
    assert mini != session["circle_id"]
    assert body["charter"]["amount"] == "5.00" and body["charter"]["cycles"] == 2
    assert body["charter"]["rotation"] == ["judge_payee", "judge_member"]
    assert [m["handle"] for m in body["members"]] == ["judge_payee", "judge_member"]
    assert body["fees"]["fee_estimate"] == "0.66"  # 3.49% of 5.00 + 0.49
    assert body["invite"]["handle"] == "judge_member" and body["invite"]["path"].startswith("/join/")
    assert body["payee_configured"] is False
    assert client.post("/api/join/judge-circle").json()["circle_id"] == mini  # one per session and run

    token = body["invite"]["token"]
    card = client.get(f"/api/join/{token}").json()
    assert card["circle"]["kind"] == "judge_mini" and card["join"]["payout_round"] == 2
    assert client.post(f"/api/join/{token}/start").status_code == 200
    plan = vault.calls[0][1]["billing_plan"]["billing_cycles"][0]
    assert plan["total_cycles"] == 2 and plan["pricing_scheme"]["price"]["value"] == "5.00"
    assert vault.calls[0][1]["request_id"] == f"tk:{session['run_id']}:{mini}:setup:judge_member"
    r = client.get(f"/join/{token}/return?approval_token_id=FAKESETUP1", follow_redirects=False)
    assert r.headers["location"].endswith("status=vaulted")
    assert jn.is_vaulted(_member(mini, "judge_member"))

    invites = client.get(f"/api/circles/{mini}/invites")
    assert invites.status_code == 200
    assert {i["handle"]: i["join_status"] for i in invites.json()} == {
        "judge_payee": "invited",
        "judge_member": "vaulted",
    }
    # the demo board is untouched and a new run gets a new mini-circle
    assert session["circle_id"] == client.get("/api/session").json()["circle_id"]
    client.post("/api/session/reset")
    assert client.post("/api/join/judge-circle").json()["circle_id"] != mini


def test_judge_mini_circle_payee_from_settings(client, session, monkeypatch):
    monkeypatch.setenv("TK_JUDGE_PAYEE_RECEIVER", "payee@example.com")
    monkeypatch.setenv("TK_JUDGE_PAYEE_VAULT_ID", "FAKEPAYEEVAULT")
    from app.config import get_settings

    get_settings.cache_clear()
    body = client.post("/api/join/judge-circle").json()
    assert body["payee_configured"] is True
    payee = _member(body["circle_id"], "judge_payee")
    assert payee.payout_receiver == "payee@example.com" and payee.join_status == "vaulted"


def test_other_sessions_cannot_list_the_mini_circle_invites(app, client, session):
    from fastapi.testclient import TestClient

    mini = client.post("/api/join/judge-circle").json()["circle_id"]
    with TestClient(app) as other:
        other.get("/api/session")
        assert other.get(f"/api/circles/{mini}/invites").status_code == 403
        assert other.post("/api/join/judge-circle").json()["circle_id"] != mini


def test_judge_join_card_names_members_in_full_and_promises_no_buffer(client, session):
    """Review: 'Members: Judge, Judge' and a $0 buffer that 'covers you' confused the judge join page."""
    body = client.post("/api/join/judge-circle").json()
    assert "any US sandbox personal account" in body["notes"][0] and "README" not in body["notes"][0]
    card = client.get(body["invite"]["path"].replace("/join/", "/api/join/")).json()["circle"]
    assert card["members"] == [
        {"handle": "judge_payee", "name": "Judge Payee", "you": False},
        {"handle": "judge_member", "name": "Judge Member", "you": True},
    ]
    assert card["buffer_seed"] == "0.00" and "buffer_advance" not in card["remedy_menu"]
