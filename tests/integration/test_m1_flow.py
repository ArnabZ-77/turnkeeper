"""M1 end-to-end through the HTTP API: session -> board -> collect -> (rules-mode treasurer) -> payout.

The PayPal sandbox (https://api-m.sandbox.paypal.com) is mocked with respx, so the real B1 client, gateway,
wrappers and the B4 engine all run; only the wire is fake. No ANTHROPIC_API_KEY -> the treasurer runs in
rules mode and walks the real MCP tools.

Toolkit note: the PayPal Agent Toolkit's `get_order_details` uses the synchronous `requests` library, which
respx cannot intercept. `ToolkitAdapter.run` is therefore replaced for this module by a test-only shim that
performs the same `GET /v2/checkout/orders/{id}` through the gateway's httpx path (`gw.call`) and returns the
toolkit's own result shape (`{"message", "status", "amount"}`), so the engine's `confirm_capture` and the
rules-mode `get_order_details` step are exercised unchanged and the call is still counted by respx.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient

from integration.conftest import (
    FEE,
    GROSS,
    MEMBERS,
    MOCK_DECLINE_LABEL,
    NET,
    PAYOUT_FEE,
    POT,
    SandboxMock,
    board_of,
    contributions,
    recent_events,
    round_row,
    start_session,
    toggle_ravi,
    wait_for_board,
    wait_for_task,
)

# --- scenario 1: clean round ----------------------------------------------------------------------


def test_scenario_1_clean_round_collects_confirms_and_pays(client: TestClient, sandbox: SandboxMock):
    sess = start_session(client)
    circle_id, run_id, sid = sess["circle_id"], sess["run_id"], sess["sid"]

    board = board_of(client, circle_id)
    assert board["circle"]["id"] == circle_id and board["circle"]["run_id"] == run_id
    assert board["circle"]["flags"]["ravi_declines"] is True
    assert board["circle"]["mandate_present"] is True
    assert [m["handle"] for m in board["members"]] and all(m["vaulted"] for m in board["members"])
    assert round_row(board, 1)["status"] == "PAID" and round_row(board, 2)["status"] == "PAID"
    assert round_row(board, 3)["status"] == "PENDING" and round_row(board, 3)["recipient"] == "dev"
    assert all(c["status"] == "PENDING" for c in contributions(board, 3).values())

    toggle_ravi(client, circle_id, on=False)
    assert board_of(client, circle_id)["circle"]["flags"]["ravi_declines"] is False

    r = client.post(f"/api/circles/{circle_id}/rounds/3/collect")
    assert r.status_code == 202, r.text
    task = r.json()
    assert task["kind"] == "collect_round" and task["agent"] is True

    def all_completed(b: dict) -> bool:
        rows = contributions(b, 3)
        return len(rows) == 6 and all(c["status"] == "COMPLETED" for c in rows.values())

    board = wait_for_board(client, circle_id, all_completed)
    rows = contributions(board, 3)
    assert sorted(rows) == sorted(MEMBERS)
    for handle, c in rows.items():
        assert c["order_id"] == f"ORD-{handle.upper()}-{c['order_id'].rsplit('-', 1)[1]}"
        assert c["order_id"] in sandbox.orders and c["capture_id"].startswith(f"CAP-{handle.upper()}-")
        assert (c["gross"], c["fee"], c["net"]) == (str(GROSS), str(FEE), str(NET))
        assert c["attempt"] == 1 and c["decline_code"] is None and c["decline_source"] is None
    # every charge carried our idempotency key and none asked the sandbox for a decline
    assert len(sandbox.order_posts) == 6
    keys = sorted(req.headers["PayPal-Request-Id"] for req in sandbox.order_posts)
    assert keys == sorted(f"tk:{run_id}:{circle_id}:3:{m}:a1" for m in MEMBERS)
    assert all("PayPal-Mock-Response" not in req.headers for req in sandbox.order_posts)
    assert all(req.headers["Authorization"] == "Bearer test-access-token" for req in sandbox.order_posts)

    # rules mode: collect -> get_order_details x6 (confirmed via the toolkit shim) -> balance -> payout
    done = wait_for_task(client, task["task_id"])
    assert done["ok"] is True and done["mode"] == "rules", done
    assert "6 charged, 0 declined" in done["summary"] and "Confirmed 6/6" in done["summary"]
    assert "Pot paid" in done["summary"], done["summary"]
    board = wait_for_board(client, circle_id, lambda b: round_row(b, 3)["status"] == "PAID")
    rows = contributions(board, 3)
    assert all(c["label"] == "Confirmed via toolkit" for c in rows.values())
    assert sandbox.get_order_route.call_count == 6
    r3 = round_row(board, 3)
    assert r3["payout"] == {
        "batch_id": "BATCH-0001",
        "item_id": "ITEM-0001",
        "status": "SUCCESS",
        "amount": str(POT),
        "fee": str(PAYOUT_FEE),  # the item's fee as the ledger booked it
    }
    assert r3["paid_at"] is not None
    assert len(sandbox.payouts) == 1
    sent = sandbox.payouts[0]
    assert sent["sender_batch_id"] == f"tk:{run_id}:{circle_id}:3:r1"
    assert sent["item"]["receiver"] == "dev@example.com" and sent["item"]["recipient_wallet"] == "PAYPAL"
    assert sent["item"]["amount"] == {"value": str(POT), "currency": "USD"}
    assert board["counters"]["rounds_paid_on_time"] == 3
    assert board["ticker"]["paid_at"] is not None and board["ticker"]["seconds"] is not None

    payout_events = recent_events(sid, "payout")
    assert payout_events, "no payout event on the session stream"
    ev = payout_events[-1]
    assert ev["round_n"] == 3 and ev["status"] == "SUCCESS" and ev["recipient"] == "dev"
    assert ev["batch_id"] == "BATCH-0001" and ev["item_id"] == "ITEM-0001" and ev["amount"] == str(POT)
    chips = [e for e in recent_events(sid, "chip") if e["round_n"] == 3 and e["status"] == "COMPLETED"]
    assert {e["member"] for e in chips} == set(MEMBERS)
    assert any(
        e["kind"] == "tool_call" and e["tool"] == "create_pot_payout" for e in recent_events(sid, "trace")
    )

    # POST payout on the already-paid round: accepted, runs, and is refused without a second PayPal payout
    r = client.post(f"/api/circles/{circle_id}/rounds/3/payout")
    assert r.status_code == 202, r.text
    assert r.json()["kind"] == "payout"
    done = wait_for_task(client, r.json()["task_id"])
    assert done["mode"] == "rules" and done["ok"] is False
    assert "already paid" in done["summary"], done["summary"]
    assert len(sandbox.payout_posts) == 1
    after = board_of(client, circle_id)
    assert round_row(after, 3)["status"] == "PAID" and round_row(after, 3)["payout"] == r3["payout"]
    assert sandbox.token_route.call_count == 1  # one token fetch, cached afterwards


# --- scenario 2: ravi declines ----------------------------------------------------------------------


def test_scenario_2_sandbox_injected_decline_blocks_the_payout(client: TestClient, sandbox: SandboxMock):
    from app.domain.policy import PolicyDenied, PolicyEngine
    from app.engine import rounds, state

    sess = start_session(client)
    circle_id, run_id, sid = sess["circle_id"], sess["run_id"], sess["sid"]
    toggle_ravi(client, circle_id, on=True)

    r = client.post(f"/api/circles/{circle_id}/rounds/3/collect")
    assert r.status_code == 202, r.text
    done = wait_for_task(client, r.json()["task_id"])
    assert done["mode"] == "rules"
    assert (
        "5 charged, 1 declined (ravi)" in done["summary"]
        and "Payout held: waiting on ravi" in done["summary"]
    )
    board = wait_for_board(
        client, circle_id, lambda b: all(c["status"] != "PENDING" for c in contributions(b, 3).values())
    )
    rows = contributions(board, 3)

    ravi = rows["ravi"]
    assert ravi["status"] == "DECLINED" and ravi["label"] == MOCK_DECLINE_LABEL
    assert ravi["decline_source"] == "api" and ravi["decline_code"] == "INSTRUMENT_DECLINED"
    assert ravi["order_id"] is None and ravi["capture_id"] is None and ravi["attempt"] == 1
    ravi_posts = [
        req
        for req in sandbox.order_posts
        if json.loads(req.content)["purchase_units"][0]["reference_id"].endswith(":ravi")
    ]
    assert len(ravi_posts) == 1
    assert json.loads(ravi_posts[0].headers["PayPal-Mock-Response"]) == {
        "mock_application_codes": "INSTRUMENT_DECLINED"
    }
    assert ravi_posts[0].headers["PayPal-Request-Id"] == f"tk:{run_id}:{circle_id}:3:ravi:a1"
    others = {h: c for h, c in rows.items() if h != "ravi"}
    assert sorted(others) == ["asha", "dev", "lin", "meena", "tomas"]
    assert all(c["status"] == "COMPLETED" and c["net"] == str(NET) for c in others.values())
    assert all(c["label"] == "Confirmed via toolkit" for c in others.values())
    assert len(sandbox.order_posts) == 6 and sandbox.get_order_route.call_count == 5
    assert round_row(board, 3)["status"] == "COLLECTING" and round_row(board, 3)["payout"] is None
    assert sandbox.payouts == []
    declined_chips = [e for e in recent_events(sid, "chip") if e["member"] == "ravi" and e["round_n"] == 3]
    assert declined_chips and declined_chips[-1]["status"] == "DECLINED"
    assert declined_chips[-1]["label"] == MOCK_DECLINE_LABEL
    assert declined_chips[-1]["decline_code"] == "INSTRUMENT_DECLINED"

    # the payout task through the API: held, no PayPal payout call
    r = client.post(f"/api/circles/{circle_id}/rounds/3/payout")
    assert r.status_code == 202, r.text
    done = wait_for_task(client, r.json()["task_id"])
    assert done["mode"] == "rules" and "Payout held: waiting on ravi" in done["summary"]
    assert sandbox.payout_posts == []

    # the engine's pay_round itself (what create_pot_payout calls) denies with a readable reason
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    result: dict[str, Any] = client.portal.call(partial(rounds.pay_round, circle_id, 3, publish=collect))
    assert result["ok"] is False
    assert result["denied"] == "contributions outstanding: ravi"
    assert result["outstanding"] == ["ravi"]

    # and the policy engine (B2) refuses a payout for a round that is not fully collected
    cs = state.circle_state(circle_id, 3)
    cs.mandate_ok = True
    assert cs.contributions["ravi"] == "DECLINED"
    with pytest.raises(PolicyDenied) as exc:
        PolicyEngine(state.load_charter(circle_id), cs).authorize("payout", recipient="dev", amount=POT)
    assert "not fully collected" in str(exc.value) and "ravi" in str(exc.value)
    assert sandbox.payout_posts == []
    assert round_row(board_of(client, circle_id), 3)["status"] == "COLLECTING"
