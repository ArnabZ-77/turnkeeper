"""The Judge Mode story, twice in a row from a fresh reset, on the live-sandbox path (respx-mocked sandbox).

Covers the leftovers of docs/live-run/2026-10-25-rerun.md end to end:
- the story is re-runnable: two complete runs back to back, distinct idempotency keys and payout batch ids;
- R2: the round 3 payout comes back PENDING and the board reaches SUCCESS by itself (bounded status polls);
- R4: the process-wide gateway settings (LIVE_MAX_INFLIGHT) run the round's calls concurrently;
- R5: Ravi's reply commits to the payday the T-3 forecast named;
- the payout amount and fee shown (step summary, board, receipt event) are the ledger's.
"""

from __future__ import annotations

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from integration.conftest import (
    PAYOUT_FEE,
    POT,
    SandboxMock,
    board_of,
    contributions,
    recent_events,
    round_row,
    start_session,
    vault_members,
    wait_for_board,
)
from integration.test_m2_recovery import FakeToolkit, toolkit  # noqa: F401 - the toolkit fixture is used by name
from integration.test_m3_story import STORY, next_step, story


def _ledger(circle_id: str, kind: str, round_n: int) -> list[str]:
    from app.engine import state

    return [r.amount for r in state.ledger_rows(circle_id) if r.kind == kind and r.round_n == round_n]


def _run_story(client: TestClient, sandbox: SandboxMock, sid: str) -> dict:
    # "Reset demo" is always allowed, also on a completed story (that is how a judge runs it again)
    r = client.post("/api/judge/story/reset")
    assert r.status_code == 200, r.text
    out = r.json()
    cid = out["session"]["circle_id"]
    run_id = out["session"]["run_id"]
    vault_members(cid)
    assert story(client)["paypal"] == "live"

    next_step(client, "advance_to_t3")
    board = board_of(client, cid)
    ravi_forecast = next(f for f in board["forecasts"] if f["member"] == "ravi" and f["round_n"] == 3)
    payday_detail = next(f["detail"] for f in ravi_forecast["factors"] if f["factor"] == "payday")

    next_step(client, "run_round3")
    rows = contributions(board_of(client, cid), 3)
    assert rows["ravi"]["status"] == "DECLINED"
    assert all(r["label"] == "Confirmed via toolkit" for m, r in rows.items() if m != "ravi")

    # R5: the reply's payday is the "next" payday the forecast showed
    payday = next_step(client, "reply_as_ravi")["result"]["payday"]
    assert payday == "2027-01-08" and f"next {payday}" in payday_detail, payday_detail
    for step in ("start_recovery", "consents", "votes", "approve", "execute"):
        next_step(client, step)
    rec = client.get(f"/api/circles/{cid}/recovery").json()
    assert rec["commitment"]["date"] == payday

    # R2: PayPal answers PENDING to the payout's own reads and the first status poll, then SUCCESS
    sandbox.pending_item_reads = 3
    out = next_step(client, "payout")
    assert out["result"]["status"] == "PENDING" and out["result"]["watching"] is True
    assert out["summary"].startswith("Pot $1,154.93 sent to Dev Mehta (PayPal fee $0.25): pending")
    board = wait_for_board(client, cid, lambda b: round_row(b, 3)["status"] == "PAID", timeout=10)
    r3 = round_row(board, 3)
    assert r3["payout"]["status"] == "SUCCESS" and r3["paid_at"]
    # amount and fee shown == the ledger's rows for the item
    assert r3["payout"]["amount"] == str(POT) and r3["payout"]["fee"] == str(PAYOUT_FEE)
    assert _ledger(cid, "payout", 3) == [str(POT)] and _ledger(cid, "payout_fee", 3) == [str(PAYOUT_FEE)]
    assert [(a["status"], a["fee"], a["amount"]) for a in r3["payout_attempts"]] == [
        ("SUCCESS", str(PAYOUT_FEE), str(POT))
    ]
    receipts = [e for e in recent_events(sid, "payout") if e.get("item_id") == r3["payout"]["item_id"]]
    assert [e["status"] for e in receipts] == ["PENDING", "SUCCESS"]
    assert all(e["amount"] == str(POT) and e["fee"] == str(PAYOUT_FEE) for e in receipts)

    next_step(client, "clean_round4")
    board = wait_for_board(client, cid, lambda b: round_row(b, 4)["status"] == "PAID", timeout=10)
    r4 = round_row(board, 4)
    assert r4["payout"]["amount"] == str(POT) and r4["payout"]["fee"] == str(PAYOUT_FEE)
    # the circle's ledger balances to zero after two pots: every net cent was paid out or paid as a fee
    assert Decimal(board["circle"]["circle_available"]) == Decimal("0.00")

    view = story(client)
    assert view["complete"] is True and [s["step"] for s in view["steps"]] == STORY
    assert all(s["status"] == "done" for s in view["steps"])
    return {"circle_id": cid, "run_id": run_id}


def test_story_runs_twice_in_a_row(
    client: TestClient,
    sandbox: SandboxMock,
    toolkit: FakeToolkit,  # noqa: F811 - the fixture imported above
    monkeypatch,
):
    from app.config import get_settings
    from app.engine import rounds
    from app.paypal.client import PayPalClient
    from app.paypal.gateway import LIVE_MAX_INFLIGHT, Gateway, get_gateway, set_gateway

    settings = get_settings()
    # One full story makes ~40 PayPal calls, so at the live 40/min budget a second run started within the same
    # minute waits for the rolling window (by design: the sandbox blocks the IP at ~50/min). The budget has its own
    # tests (tests/paypal/test_gateway.py); here the window is widened so two runs fit in a test's time.
    set_gateway(
        Gateway(PayPalClient(settings), rate_per_min=600, min_gap_s=0.02, max_inflight=LIVE_MAX_INFLIGHT)
    )
    monkeypatch.setattr(rounds, "PAYOUT_WATCH_ENABLED", True)
    monkeypatch.setattr(rounds, "PAYOUT_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(rounds, "PAYOUT_POLL_MAX_S", 5.0)

    sid = start_session(client)["sid"]
    first = _run_story(client, sandbox, sid)
    second = _run_story(client, sandbox, sid)

    assert first["circle_id"] != second["circle_id"] and first["run_id"] != second["run_id"]
    # 2 runs x (round 3 + round 4): four payouts, each with its own batch id; 2 x (6 + 6) charges with unique keys
    assert len(sandbox.payouts) == 4
    assert len({p["sender_batch_id"] for p in sandbox.payouts}) == 4
    keys = [req.headers["PayPal-Request-Id"] for req in sandbox.order_posts]
    assert len(keys) == 24 and len(set(keys)) == 24
    assert all(first["run_id"] in k for k in keys[:12]) and all(second["run_id"] in k for k in keys[12:])
    # no 429 from the (mocked) sandbox, and every call went through the one gateway
    gw = get_gateway()
    assert gw.max_inflight == LIVE_MAX_INFLIGHT and gw.stats["rate_limited"] == 0
    assert gw.stats["calls"] == len(sandbox.router.calls) - sandbox.token_route.call_count

    replay = TestClient(client.app).get("/api/replay").json()
    assert replay["source"] == "recorded" and replay["paypal_mode"] == "live"
    assert replay["run_id"] == second["run_id"]
    assert "@example.com" not in json.dumps(replay)
