"""Member Passbook (flag TK_PASSBOOK): capability-gated, built from stored rows, dignity rule, receipts match the DB."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from recovery_fakes import FakeInvoiceToolkit  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.engine import capabilities, recovery, state  # noqa: E402
from app.models import Contribution  # noqa: E402
from routes.test_member_mandates_routes import _next, _to_voting  # noqa: E402

H = "X-Turnkeeper-Capability"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    monkeypatch.setenv("TK_LLM_API_KEY", "")
    monkeypatch.setenv("TK_PASSBOOK", "1")
    get_settings.cache_clear()
    state.clear_paypal_balance()
    yield
    state.clear_paypal_balance()
    get_settings.cache_clear()


@pytest.fixture
def executed(client, session, monkeypatch) -> str:
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    cid = _to_voting(client, tk)
    for step in ("consents", "votes", "approve", "execute"):
        _next(client, step)
    return cid


def _get(client, token: str):
    return client.get("/api/passbook", headers={H: token})


def test_flag_off_is_404_and_token_is_required(client, monkeypatch):
    assert client.get("/api/passbook").status_code == 401
    monkeypatch.setenv("TK_PASSBOOK", "")
    assert client.get("/api/passbook", headers={H: "x"}).status_code == 404


def test_tampered_or_expired_token_is_403(client, executed, monkeypatch):
    token = capabilities.mint_read(executed, "ravi")["token"]
    assert _get(client, token[:-3] + "AAA").status_code == 403
    monkeypatch.setattr(capabilities, "_now", lambda: 10**10)
    r = _get(client, token)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "CAPABILITY_EXPIRED"


def test_ravi_sees_his_covered_share_and_his_invoice(client, executed):
    body = _get(client, capabilities.mint_read(executed, "ravi")["token"]).json()
    assert body["member"]["handle"] == "ravi" and body["label"] == "demo member view, no login"
    assert body["this_round"]["mine"] == "COVERED" and body["this_round"]["covered_by_buffer"] == 1
    assert body["repayment"] and body["repayment"]["amount"] == "200.00"
    assert body["turn"]["moved"] is True  # the swap moved Ravi's turn
    assert body["turn"]["expected_pot"] == "1154.93"  # 6 x 192.53 net, less the 0.25 Payouts fee
    assert body["if_someone_misses"]["charter_sha256"] == body["circle"]["charter_sha256"]


def test_dignity_rule_nobody_else_is_named(client, executed):
    for member in ("tomas", "dev", "asha"):
        body = _get(client, capabilities.mint_read(executed, member)["token"]).json()
        text = json.dumps(body).lower()
        assert "ravi" not in text, member
        assert body["repayment"] is None  # only the member who owes it sees the invoice
        assert body["this_round"]["covered_by_buffer"] == 1 and body["this_round"]["mine"] == "COMPLETED"


def test_receipt_ids_match_the_database(client, executed):
    body = _get(client, capabilities.mint_read(executed, "dev")["token"]).json()
    with session_scope() as db:
        rows = {
            c.round_n: c
            for c in db.exec(
                __import__("sqlmodel")
                .select(Contribution)
                .where(Contribution.circle_id == executed, Contribution.member_handle == "dev")
            ).all()
        }
    assert body["payments"]
    for p in body["payments"]:
        row = rows[p["round_n"]]
        assert p["capture_id"] == row.capture_id
        chain = {link["step"]: link["value"] for link in p["receipt"]}
        assert chain["charter"].startswith(body["circle"]["charter_sha256"][:12])
        if row.capture_id:
            assert chain["capture"] == row.capture_id
        if row.request_id:
            assert chain["request_id"] == row.request_id


def test_covered_receipt_carries_the_remedy_chain(client, executed):
    body = _get(client, capabilities.mint_read(executed, "ravi")["token"]).json()
    covered = next(p for p in body["payments"] if p["status"] == "COVERED")
    steps = [link["step"] for link in covered["receipt"]]
    assert "remedy_set_sha" in steps and "organizer_approval" in steps
    assert steps.index("charter") < steps.index("remedy_set_sha") < steps.index("organizer_approval")


def test_qr_is_only_for_the_member_who_owes(client, executed):
    assert (
        client.get(
            "/api/passbook/qr", headers={H: capabilities.mint_read(executed, "tomas")["token"]}
        ).status_code
        == 404
    )
    r = client.get("/api/passbook/qr", headers={H: capabilities.mint_read(executed, "ravi")["token"]})
    assert r.status_code in (200, 404)  # the fake toolkit may not return a QR image
    if r.status_code == 200:
        assert r.headers["content-type"] == "image/png"


def test_links_are_for_the_owning_session_and_open_the_right_member(client, executed):
    assert client.post("/api/passbook/links/someone-else").status_code == 403
    links = client.post(f"/api/passbook/links/{executed}").json()["links"]
    assert {link["member"] for link in links} == {"asha", "ravi", "meena", "tomas", "lin", "dev"}
    tomas = next(link for link in links if link["member"] == "tomas")
    assert tomas["path"].startswith("/m#tkc1.")
    assert _get(client, tomas["path"].split("#", 1)[1]).json()["member"]["handle"] == "tomas"
