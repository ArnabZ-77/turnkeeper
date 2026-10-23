"""Recovery routes and Judge Mode actions (B6), end to end through the app with PayPal and the toolkit faked."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from recovery_fakes import FAKE_INVOICE_ID, FakeInvoiceToolkit, use_swappable_rotation  # noqa: E402

from app.engine import recovery, state, tasks  # noqa: E402
from app.engine.events import get_event_bus  # noqa: E402
from routes.conftest import wait_for_task  # noqa: E402

SWAPPED = ["meena", "asha", "dev", "lin", "tomas", "ravi"]


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """No live model in these tests, whatever .env holds (the recovery path must work without one)."""
    from app.config import get_settings

    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    monkeypatch.setenv("TK_LLM_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def toolkit(monkeypatch) -> FakeInvoiceToolkit:
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    return tk


@pytest.fixture
def no_agent(monkeypatch):
    monkeypatch.setattr(tasks, "agent_available", lambda: (False, "agent disabled in this test"))


@pytest.fixture
def declined(client, vaulted, paypal, no_agent):
    """Round 3 run through Judge Mode (engine fallback) with ravi declining, on the swappable rotation."""
    cid = vaulted["circle_id"]
    use_swappable_rotation(cid)
    r = client.post("/api/judge/run_round", json={"circle_id": cid, "round_n": 3})
    assert r.status_code == 200, r.text
    wait_for_task(client, r.json()["task_id"])
    ravi = [
        c
        for c in client.get(f"/api/circles/{cid}").json()["contributions"]
        if c["round_n"] == 3 and c["member"] == "ravi"
    ]
    assert ravi[0]["status"] == "DECLINED"
    return vaulted


def _judge(client, action, cid, **extra):
    return client.post(f"/api/judge/{action}", json={"circle_id": cid, **extra})


def test_full_recovery_flow_through_routes(client, declined, toolkit, paypal):
    cid, sid = declined["circle_id"], declined["sid"]

    r = _judge(client, "reply_as_ravi", cid)
    assert r.status_code == 200 and r.json()["payday"] == "2027-01-01" and r.json()["message"]["lang"] == "hi"

    r = client.post(f"/api/circles/{cid}/recovery/start", json={})
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["kind"] == "recover" and started["round_n"] == 3 and started["member"] == "ravi"
    done = wait_for_task(client, started["task_id"])
    assert done["ok"] is True and done["mode"] == "rules"

    view = client.get(f"/api/circles/{cid}/recovery").json()
    assert view["status"] == "voting" and view["commitment"]["date"] == "2027-01-01"
    assert [r["type"] for r in view["remedies"]] == ["buffer_advance", "repay_by_invoice", "turn_swap"]
    assert view["votes"]["needed"] == 4 and view["consents"]["needed"] == ["ravi", "tomas"]
    assert any(m["direction"] == "in" for m in view["inbox"]) and any(
        m["direction"] == "out" for m in view["inbox"]
    )

    # refused until quorum + consents
    r = _judge(client, "approve_as_priya", cid)
    assert r.status_code == 409 and "yes votes" in r.json()["detail"]

    r = _judge(client, "consent_as_affected", cid)
    assert r.status_code == 200 and r.json()["consents"]["given"] == ["ravi", "tomas"]
    r = _judge(client, "vote_as_members", cid)
    body = r.json()
    assert r.status_code == 200 and body["votes"]["yes"] == 4 and body["votes"]["passed"] is True
    assert "ravi" not in body["cast"] and "tomas" not in body["cast"]

    r = _judge(client, "approve_as_priya", cid)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["ok"] is True and result["status"] == "executed"
    assert result["payout"]["ok"] is True and result["payout"]["recipient"] == "dev"

    board = client.get(f"/api/circles/{cid}").json()
    assert board["circle"]["rotation"] == SWAPPED
    assert board["counters"]["shortfalls_covered"] == 1
    assert board["recovery"]["status"] == "executed"
    assert board["recovery"]["votes"] == {
        "yes": 4,
        "no": 0,
        "needed": 4,
        "total": 6,
        "passed": True,
        "closes_at": board["recovery"]["votes"]["closes_at"],
    }
    assert board["recovery"]["approval"]["status"] == "approved"
    assert board["recovery"]["invoice"]["id"] == FAKE_INVOICE_ID
    ravi3 = [c for c in board["contributions"] if c["round_n"] == 3 and c["member"] == "ravi"][0]
    assert ravi3["status"] == "COVERED" and ravi3["label"] == "Covered by buffer"
    assert board["rounds"][2]["status"] == "PAID"
    types = [e["type"] for e in get_event_bus().recent(sid, 200)]
    assert "schedule_changed" in types and "payout" in types

    qr = client.get(f"/api/circles/{cid}/recovery/qr")
    assert (
        qr.status_code == 200
        and qr.headers["content-type"] == "image/png"
        and qr.content.startswith(b"\x89PNG")
    )

    r = _judge(client, "mark_invoice_paid", cid)
    assert r.status_code == 200 and r.json()["simulated"] is True and r.json()["repaid"] == "192.53"
    board = client.get(f"/api/circles/{cid}").json()
    assert board["circle"]["buffer_balance"] == "250.00"
    ravi3 = [c for c in board["contributions"] if c["round_n"] == 3 and c["member"] == "ravi"][0]
    assert ravi3["label"] == "Round 3: covered, not missed"
    assert board["recovery"]["status"] == "repaid" and board["recovery"]["invoice"]["status"] == "PAID"
    ledger = [r for r in state.ledger_rows(cid) if r.kind == "advance_repaid"]
    assert ledger[0].ref == f"simulated:{FAKE_INVOICE_ID}"
    again = _judge(client, "mark_invoice_paid", cid)
    assert again.status_code == 200 and again.json()["duplicate"] is True


def test_recovery_refusals(client, vaulted, no_agent, toolkit):
    cid = vaulted["circle_id"]
    assert client.post(f"/api/circles/{cid}/recovery/start", json={}).status_code == 409  # nothing declined
    assert _judge(client, "approve_as_priya", cid).status_code == 409  # no proposal
    assert _judge(client, "vote_as_members", cid).status_code == 409
    assert _judge(client, "mark_invoice_paid", cid).status_code == 409
    assert client.get(f"/api/circles/{cid}/recovery").json()["status"] == "none"
    assert client.get(f"/api/circles/{cid}/recovery/qr").status_code == 404
    assert client.post("/api/circles/zzzzzz/recovery/start", json={}).status_code == 403
    assert _judge(client, "reply_as_ravi", "zzzzzz").status_code == 403
    assert _judge(client, "explode", cid).status_code == 404  # the judge router still answers the rest


def test_agent_without_a_proposal_falls_back_to_rules(client, declined, toolkit, monkeypatch):
    cid = declined["circle_id"]
    calls = []

    async def fake_run_task(task, *, publish, live_allowed=True):
        calls.append(task.kind)
        return SimpleNamespace(ok=True, mode="live", summary="read the history")

    import app.agent.treasurer as treasurer

    monkeypatch.setattr(tasks, "agent_available", lambda: (True, ""))
    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    r = client.post(f"/api/circles/{cid}/recovery/start", json={"round_n": 3, "member": "ravi"})
    assert r.status_code == 202 and r.json()["agent"] is True
    done = wait_for_task(client, r.json()["task_id"])
    assert calls == ["recover"] and done["mode"] == "rules" and done["ok"] is True
    assert recovery.latest_set(cid).source == "rules"


def test_agent_proposal_is_kept(client, declined, toolkit, monkeypatch):
    cid = declined["circle_id"]

    async def fake_run_task(task, *, publish, live_allowed=True):
        await recovery.propose(task.circle_id, task.round_n, "ravi", None, source="agent", publish=publish)
        return SimpleNamespace(ok=True, mode="live", summary="proposed")

    import app.agent.treasurer as treasurer

    monkeypatch.setattr(tasks, "agent_available", lambda: (True, ""))
    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    r = client.post(f"/api/circles/{cid}/recovery/start", json={})
    done = wait_for_task(client, r.json()["task_id"])
    assert done["mode"] == "live" and done["summary"] == "proposed"
    latest = recovery.latest_set(cid)
    # The agent stopped after proposing: the runner opens the vote and requests Priya's approval itself, so the
    # Consent and Vote buttons always have an open vote to act on.
    assert latest.source == "agent" and latest.status == "voting"
    assert latest.approval_requested_at is not None


# --- review fixes (fixer, 2026-10-23) ----------------------------------------------------------------------


def test_start_recovery_refuses_a_member_who_did_not_decline(client, declined, toolkit):
    cid = declined["circle_id"]
    r = client.post(f"/api/circles/{cid}/recovery/start", json={"round_n": 5, "member": "asha"})
    assert r.status_code == 409 and "only a declined contribution" in r.json()["detail"]
    r = client.post(f"/api/circles/{cid}/recovery/start", json={"round_n": 3, "member": "asha"})
    assert r.status_code == 409
    assert recovery.latest_set(cid) is None


def test_simulated_decline_reaches_the_whole_recovery_path_without_vault_ids(
    client, session, toolkit, no_agent
):
    """A cold judge (no seeded vault ids, or no credentials): the simulated decline replaces Run Round 3."""
    cid = session["circle_id"]
    assert session["vault_seeded"] is False
    use_swappable_rotation(cid)
    r = _judge(client, "simulate_round3_decline", cid)
    assert r.status_code == 200, r.text
    assert r.json()["simulated"] is True and r.json()["member"] == "ravi"
    board = client.get(f"/api/circles/{cid}").json()
    r3 = {c["member"]: c for c in board["contributions"] if c["round_n"] == 3}
    assert r3["ravi"]["status"] == "DECLINED" and r3["ravi"]["decline_source"] == "simulated"
    assert r3["ravi"]["label"] == "API decline: simulated (no live sandbox for this circle)"
    assert all(
        c["status"] == "COMPLETED" and c["order_id"].startswith("REPLAY-")
        for m, c in r3.items()
        if m != "ravi"
    )

    assert _judge(client, "reply_as_ravi", cid).status_code == 200
    r = client.post(f"/api/circles/{cid}/recovery/start", json={})
    assert r.status_code == 202, r.text
    assert wait_for_task(client, r.json()["task_id"])["ok"] is True
    assert _judge(client, "consent_as_affected", cid).status_code == 200
    assert _judge(client, "vote_as_members", cid).status_code == 200
    r = _judge(client, "approve_as_priya", cid)
    assert r.status_code == 200 and r.json()["status"] == "executed", r.text
    assert r.json()["payout"]["skipped"].startswith("simulated round")  # no real money on simulated captures
    # fixer: credentials are configured here, but a simulated round never triggers a live toolkit invoice
    assert toolkit.calls == [] and toolkit.prepared == 0
    r = _judge(client, "mark_invoice_paid", cid)
    assert r.status_code == 200 and r.json()["handled"] is True


def test_simulated_decline_is_refused_when_the_sandbox_is_live(client, vaulted):
    r = _judge(client, "simulate_round3_decline", vaulted["circle_id"])
    assert r.status_code == 409 and "Run Round 3" in r.json()["detail"]
