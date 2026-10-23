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
import time
from decimal import Decimal
from functools import partial
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import SANDBOX_BASE_URL

GROSS = Decimal("200.00")
FEE = Decimal("7.47")  # 3.49% + 0.49 on 200.00
NET = GROSS - FEE  # 192.53
PAYOUT_FEE = Decimal("0.25")
POT = NET * 6 - PAYOUT_FEE  # 1154.93
MOCK_DECLINE_LABEL = "API decline: sandbox-injected"
MEMBERS = ["asha", "ravi", "meena", "tomas", "lin", "dev"]


# --- the fake sandbox ---------------------------------------------------------------------------


class SandboxMock:
    """Stateful respx routes for the sandbox endpoints the M1 flow touches."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.orders: dict[str, dict] = {}
        self.order_posts: list[httpx.Request] = []
        self.payouts: list[dict] = []
        self.payout_posts: list[httpx.Request] = []
        self.token_route = router.post("/v1/oauth2/token").mock(
            return_value=httpx.Response(
                200, json={"access_token": "test-access-token", "token_type": "Bearer", "expires_in": 32400}
            )
        )
        router.post("/v2/checkout/orders").mock(side_effect=self._create_order)
        self.get_order_route = router.get(path__regex=r"^/v2/checkout/orders/(?P<order_id>[^/]+)$").mock(
            side_effect=self._get_order
        )
        router.get("/v1/reporting/balances").mock(
            return_value=httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "currency": "USD",
                            "primary": True,
                            "total_balance": {"currency_code": "USD", "value": "5000.00"},
                            "available_balance": {"currency_code": "USD", "value": "5000.00"},
                            "withheld_balance": {"currency_code": "USD", "value": "0.00"},
                        }
                    ],
                    "account_id": "TESTACCOUNT",
                },
            )
        )
        router.post("/v1/payments/payouts").mock(side_effect=self._create_payout)
        router.get(path__regex=r"^/v1/payments/payouts/(?P<batch_id>[^/]+)$").mock(
            side_effect=self._get_batch
        )
        router.get(path__regex=r"^/v1/payments/payouts-item/(?P<item_id>[^/]+)$").mock(
            side_effect=self._get_item
        )

    # orders
    def _create_order(self, request: httpx.Request) -> httpx.Response:
        self.order_posts.append(request)
        body = json.loads(request.content)
        unit = body["purchase_units"][0]
        reference_id = unit["reference_id"]
        member = reference_id.split(":")[-1]
        if request.headers.get("PayPal-Mock-Response"):
            return httpx.Response(
                422,
                json={
                    "name": "UNPROCESSABLE_ENTITY",
                    "message": "The requested action could not be performed, semantically incorrect, "
                    "or failed business validation.",
                    "debug_id": "testdebugid0001",
                    "details": [
                        {
                            "issue": "INSTRUMENT_DECLINED",
                            "description": "The instrument presented was either declined by the processor "
                            "or bank, or it can't be used for this payment.",
                        }
                    ],
                },
            )
        n = len(self.orders) + 1
        order_id = f"ORD-{member.upper()}-{n:03d}"
        capture_id = f"CAP-{member.upper()}-{n:03d}"
        value = unit["amount"]["value"]
        currency = unit["amount"]["currency_code"]
        gross = Decimal(value)
        order = {
            "id": order_id,
            "status": "COMPLETED",
            "intent": "CAPTURE",
            "payment_source": {"paypal": {"vault_id": body["payment_source"]["paypal"]["vault_id"]}},
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "payments": {
                        "captures": [
                            {
                                "id": capture_id,
                                "status": "COMPLETED",
                                "amount": {"currency_code": currency, "value": str(gross)},
                                "final_capture": True,
                                "seller_receivable_breakdown": {
                                    "gross_amount": {"currency_code": currency, "value": str(gross)},
                                    "paypal_fee": {"currency_code": currency, "value": str(FEE)},
                                    "net_amount": {"currency_code": currency, "value": str(gross - FEE)},
                                },
                            }
                        ]
                    },
                }
            ],
        }
        self.orders[order_id] = order
        return httpx.Response(201, json=order)

    def _get_order(self, request: httpx.Request, order_id: str) -> httpx.Response:
        order = self.orders.get(order_id)
        if order is None:
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "order not found"})
        return httpx.Response(200, json=order)

    # payouts
    def _create_payout(self, request: httpx.Request) -> httpx.Response:
        self.payout_posts.append(request)
        body = json.loads(request.content)
        n = len(self.payouts) + 1
        rec = {
            "batch_id": f"BATCH-{n:04d}",
            "item_id": f"ITEM-{n:04d}",
            "sender_batch_id": body["sender_batch_header"]["sender_batch_id"],
            "item": body["items"][0],
        }
        self.payouts.append(rec)
        return httpx.Response(
            201, json={"batch_header": {"payout_batch_id": rec["batch_id"], "batch_status": "PENDING"}}
        )

    def _item_json(self, rec: dict) -> dict:
        return {
            "payout_item_id": rec["item_id"],
            "transaction_id": f"TXN-{rec['item_id']}",
            "transaction_status": "SUCCESS",
            "payout_item_fee": {"currency": "USD", "value": str(PAYOUT_FEE)},
            "payout_batch_id": rec["batch_id"],
            "payout_item": rec["item"],
        }

    def _find(self, **key: str) -> dict | None:
        for rec in self.payouts:
            if all(rec[k] == v for k, v in key.items()):
                return rec
        return None

    def _get_batch(self, request: httpx.Request, batch_id: str) -> httpx.Response:
        rec = self._find(batch_id=batch_id)
        if rec is None:
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "batch not found"})
        return httpx.Response(
            200,
            json={
                "batch_header": {"payout_batch_id": batch_id, "batch_status": "SUCCESS"},
                "items": [self._item_json(rec)],
            },
        )

    def _get_item(self, request: httpx.Request, item_id: str) -> httpx.Response:
        rec = self._find(item_id=item_id)
        if rec is None:
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "item not found"})
        return httpx.Response(200, json=self._item_json(rec))


# --- fixtures -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch):
    """Rules mode, fake sandbox credentials, fresh DB/bus/tasks, a fresh real Gateway and the toolkit shim."""
    from app.config import get_settings
    from app.db import reset_db
    from app.engine import rounds, tasks
    from app.engine.events import reset_event_bus
    from app.paypal import toolkit_adapter
    from app.paypal.client import PayPalClient
    from app.paypal.gateway import Gateway, set_gateway

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # rules mode
    monkeypatch.setenv("TURNKEEPER_FREEZE", "0")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-TEST")
    monkeypatch.setenv("TK_SEED_VAULT_IDS", "")  # placeholders; the test vaults members itself
    monkeypatch.setenv("TK_MAX_ACTIVE_SESSIONS", "6")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.paypal_base_url == SANDBOX_BASE_URL
    reset_db()
    reset_event_bus()
    tasks._TASKS.clear()
    monkeypatch.setattr(rounds, "_TOOLKIT", None)

    # A real Gateway + PayPalClient (httpx -> respx). The gap is shortened so the ~17 calls of a round take
    # well under a second; FIFO ordering and the per-minute budget still apply.
    gateway = Gateway(PayPalClient(settings), rate_per_min=settings.paypal_rate_per_min, min_gap_s=0.02)
    set_gateway(gateway)

    async def httpx_get_order_details(self, method: str, params: dict, *, request_id: str | None = None):
        """Test-only shim: the toolkit's `get_order_details` through httpx instead of `requests` (see module doc)."""
        assert method == "get_order_details", method
        await self.prepare()
        resp = await self._gw.call("GET", f"/v2/checkout/orders/{params['order_id']}")
        order = resp.data
        captures = ((order.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures") or [{}]
        amount = captures[0].get("amount") or {}
        return {
            "message": f"The PayPal order {params['order_id']} has been successfully captured.",
            "status": order.get("status"),
            "amount": f"{amount.get('currency_code')} {amount.get('value')}" if amount else "N/A",
        }

    monkeypatch.setattr(toolkit_adapter.ToolkitAdapter, "run", httpx_get_order_details)
    yield
    set_gateway(None)
    get_settings.cache_clear()


@pytest.fixture
def sandbox():
    with respx.mock(base_url=SANDBOX_BASE_URL, assert_all_called=False, assert_all_mocked=True) as router:
        yield SandboxMock(router)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def vault_members(circle_id: str) -> None:
    from app.db import session_scope
    from app.models import MemberRow

    with session_scope() as db:
        for m in db.exec(select(MemberRow).where(MemberRow.circle_id == circle_id)).all():
            m.vault_id = f"TESTVAULT{m.handle.upper()}"
            m.customer_id = f"TESTCUST{m.handle.upper()}"
            m.payout_receiver = f"{m.handle}@example.com"
            db.add(m)


# --- helpers ------------------------------------------------------------------------------------


def board_of(client: TestClient, circle_id: str) -> dict:
    r = client.get(f"/api/circles/{circle_id}")
    assert r.status_code == 200, r.text
    return r.json()


def round_row(board: dict, n: int) -> dict:
    return next(r for r in board["rounds"] if r["n"] == n)


def contributions(board: dict, n: int) -> dict[str, dict]:
    return {c["member"]: c for c in board["contributions"] if c["round_n"] == n}


def wait_for_board(client: TestClient, circle_id: str, ready, timeout: float = 20.0) -> dict:
    """Poll GET /api/circles/{id} until `ready(board)` holds (the agent runs in the background)."""
    deadline = time.monotonic() + timeout
    while True:
        board = board_of(client, circle_id)
        if ready(board):
            return board
        if time.monotonic() > deadline:
            raise AssertionError(f"board never became ready within {timeout}s: {json.dumps(board)[:800]}")
        time.sleep(0.1)


def wait_for_task(client: TestClient, task_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/tasks/{task_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["done"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish within {timeout}s")


def recent_events(sid: str, type_: str) -> list[dict]:
    from app.engine.events import get_event_bus

    return [e for e in get_event_bus().recent(sid, limit=500) if e.get("type") == type_]


def start_session(client: TestClient) -> dict:
    r = client.get("/api/session")
    assert r.status_code == 200, r.text
    sess = r.json()
    assert set(sess) == {"sid", "run_id", "circle_id", "live_ai", "paypal_live", "frozen"}
    assert sess["live_ai"] is False and sess["frozen"] is False
    assert client.cookies.get("tk_sid") == sess["sid"]
    vault_members(sess["circle_id"])
    return sess


def toggle_ravi(client: TestClient, circle_id: str, on: bool) -> dict:
    r = client.post("/api/judge/toggle_ravi_declines", json={"circle_id": circle_id, "on": on})
    assert r.status_code == 200, r.text
    assert r.json()["flags"]["ravi_declines"] is on
    return r.json()


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
