"""Shared fixtures for the HTTP-level integration tests (M1 flow, M2 recovery).

The PayPal sandbox (https://api-m.sandbox.paypal.com) is mocked with respx, so the real B1 client, gateway,
wrappers and the B4 engine all run; only the wire is fake. No LLM key (tests/conftest.py forces
TK_LLM_PROVIDER=none) -> the treasurer runs in rules mode unless a test injects a fake provider.

Toolkit note: the PayPal Agent Toolkit's `get_order_details` uses the synchronous `requests` library, which
respx cannot intercept. `ToolkitAdapter.run` is therefore replaced by `httpx_get_order_details`, a test-only shim
that performs the same `GET /v2/checkout/orders/{id}` through the gateway's httpx path (`gw.call`) and returns
the toolkit's own result shape (`{"message", "status", "amount"}`), so the engine's `confirm_capture` and the
rules-mode `get_order_details` step are exercised unchanged and the call is still counted by respx.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

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
        # R2: the next N payout reads (batch or item) answer PENDING with no fee, as the real sandbox does for
        # ~18 s after a batch is created; then SUCCESS with the 0.25 fee
        self.pending_item_reads = 0
        self.item_reads = 0
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
        self.item_reads += 1
        if self.pending_item_reads > 0:
            self.pending_item_reads -= 1
            return {
                "payout_item_id": rec["item_id"],
                "transaction_status": "PENDING",
                "payout_batch_id": rec["batch_id"],
                "payout_item": rec["item"],
            }
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


# --- the toolkit shim -------------------------------------------------------------------------


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
    assert set(sess) == {"run_id", "circle_id", "live_ai", "paypal_live", "frozen", "vault_seeded"}
    assert sess["live_ai"] is False and sess["frozen"] is False
    sess["sid"] = client.cookies.get("tk_sid")  # the id is the HttpOnly cookie only, never in the body
    assert sess["sid"]
    vault_members(sess["circle_id"])
    return sess


def toggle_ravi(client: TestClient, circle_id: str, on: bool) -> dict:
    r = client.post("/api/judge/toggle_ravi_declines", json={"circle_id": circle_id, "on": on})
    assert r.status_code == 200, r.text
    assert r.json()["flags"]["ravi_declines"] is on
    return r.json()
