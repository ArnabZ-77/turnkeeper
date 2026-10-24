"""ToolkitAdapter: cached-token injection, PayPal-Request-Id, sandbox context, gateway routing, error mapping."""

from __future__ import annotations

import json

import pytest
import requests

from app.paypal.client import PayPalError
from app.paypal.gateway import Gateway
from app.paypal.toolkit_adapter import (
    DEFAULT_ACTIONS,
    ToolkitAdapter,
    normalize_actions,
    parse_toolkit_result,
)

BASE = "https://api-m.sandbox.paypal.com"
ORDER_ID = "5O190127TN364715T"
ORDER = {
    "id": ORDER_ID,
    "status": "COMPLETED",
    "purchase_units": [
        {
            "payments": {
                "captures": [
                    {"id": "C1", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "200.00"}}
                ]
            }
        }
    ],
}


class FakeResponse:
    def __init__(self, status: int, payload: dict, headers: dict):
        self.status_code = status
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(payload)
        self.request = type("Req", (), {"headers": dict(headers)})()

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)


@pytest.fixture
def toolkit_http(monkeypatch):
    """Capture the toolkit's sync HTTP calls; fail loudly if it ever tries to fetch its own token."""
    calls: list[dict] = []
    answers: list[FakeResponse] = []

    def fake_get(url, headers=None, **kw):
        calls.append({"method": "GET", "url": url, "headers": dict(headers or {})})
        resp = answers.pop(0) if answers else FakeResponse(200, ORDER, headers or {})
        resp.request.headers = dict(headers or {})
        return resp

    def fake_post(url, **kw):
        raise AssertionError(f"unexpected sync POST to {url} (token fetch bypass broken?)")

    monkeypatch.setattr("paypal_agent_toolkit.shared.paypal_client.requests.get", fake_get)
    monkeypatch.setattr("paypal_agent_toolkit.shared.paypal_client.requests.post", fake_post)
    return {"calls": calls, "answers": answers}


async def test_default_actions_enable_the_contract_tools(client, gateway):
    adapter = ToolkitAdapter(gateway, client)
    names = adapter.tool_names()
    assert {
        "get_order_details",
        "create_invoice",
        "send_invoice",
        "generate_invoice_qr_code",
        "send_invoice_reminder",
        "get_invoice",
        "list_disputes",
        "list_transactions",
    } <= set(names)
    assert "create_order" not in names and "pay_order" not in names
    assert normalize_actions(DEFAULT_ACTIONS)["invoices"]["generateQRC"] is True
    assert normalize_actions(DEFAULT_ACTIONS)["invoices"]["sendReminder"] is True
    assert normalize_actions({"invoices": {"generateQRC": True}}) == {"invoices": {"generateQRC": True}}
    assert adapter.context.sandbox is True
    assert adapter.sync_client.base_url == BASE
    assert adapter.api._paypal_client is adapter.sync_client
    assert [s["method"] for s in adapter.tool_specs()] == names


async def test_run_injects_cached_token_and_request_id(client, gateway, paypal_mock, toolkit_http):
    records: list[dict] = []
    gateway.record = records.append
    adapter = ToolkitAdapter(gateway, client)
    await adapter.prepare()
    assert paypal_mock["token"].call_count == 1
    result = await adapter.run(
        "get_order_details", {"order_id": ORDER_ID}, request_id="tk:abcdefgh:c1x2y3:3:asha:a1"
    )
    assert result["status"] == "COMPLETED"
    assert result["raw"]["id"] == ORDER_ID
    assert len(toolkit_http["calls"]) == 1
    call = toolkit_http["calls"][0]
    assert call["url"] == f"{BASE}/v2/checkout/orders/{ORDER_ID}"
    assert call["headers"]["Authorization"] == "Bearer tok-1"
    assert call["headers"]["PayPal-Request-Id"] == "tk:abcdefgh:c1x2y3:3:asha:a1"
    assert call["headers"]["Content-Type"] == "application/json"
    assert paypal_mock["token"].call_count == 1  # the sync client never fetched its own token
    assert (
        records[-1]["method"] == "TOOLKIT"
        and records[-1]["path"] == "get_order_details"
        and records[-1]["status"] == 200
    )
    # order_details() unwraps the toolkit envelope
    assert (await adapter.order_details(ORDER_ID))["id"] == ORDER_ID
    assert "PayPal-Request-Id" not in toolkit_http["calls"][1]["headers"]


async def test_run_refreshes_token_when_none_is_cached(client, gateway, paypal_mock, toolkit_http):
    adapter = ToolkitAdapter(gateway, client)
    assert client.cached_token() is None
    with pytest.raises(RuntimeError, match="prepare"):
        adapter.sync_client.get_access_token()
    await adapter.run("get_order_details", {"order_id": ORDER_ID})  # run() prepares on its own
    assert toolkit_http["calls"][0]["headers"]["Authorization"] == "Bearer tok-1"


async def test_http_error_becomes_paypal_error(client, gateway, toolkit_http):
    adapter = ToolkitAdapter(gateway, client)
    toolkit_http["answers"].append(
        FakeResponse(
            404,
            {
                "name": "RESOURCE_NOT_FOUND",
                "message": "The specified resource does not exist.",
                "debug_id": "x1",
                "details": [{"issue": "INVALID_RESOURCE_ID"}],
            },
            {},
        )
    )
    with pytest.raises(PayPalError) as exc:
        await adapter.run("get_order_details", {"order_id": ORDER_ID})
    assert exc.value.status == 404
    assert exc.value.name == "RESOURCE_NOT_FOUND"
    assert exc.value.issue_codes == ["INVALID_RESOURCE_ID"]
    assert exc.value.path == "get_order_details"


async def test_401_from_toolkit_refreshes_and_retries_once(client, gateway, paypal_mock, toolkit_http):
    from tests.paypal.conftest import token_response

    paypal_mock["token"].side_effect = [token_response("tok-1"), token_response("tok-2")]
    adapter = ToolkitAdapter(gateway, client)
    toolkit_http["answers"].append(FakeResponse(401, {"error": "invalid_token"}, {}))
    result = await adapter.run("get_order_details", {"order_id": ORDER_ID})
    assert result["raw"]["id"] == ORDER_ID
    assert [c["headers"]["Authorization"] for c in toolkit_http["calls"]] == ["Bearer tok-1", "Bearer tok-2"]


async def test_disabled_tool_and_bad_params_are_rejected(client, gateway, toolkit_http):
    adapter = ToolkitAdapter(gateway, client, actions={"orders": {"get": True}})
    assert adapter.tool_names() == ["get_order_details"]
    with pytest.raises(ValueError, match="not enabled"):
        await adapter.run("create_invoice", {})
    with pytest.raises(
        ValueError
    ):  # pydantic ValidationError (a ValueError) inside the toolkit: bad order id pattern
        await adapter.run("get_order_details", {"order_id": "not an order id"})
    assert toolkit_http["calls"] == []


async def test_toolkit_calls_share_the_fifo_queue(client, paypal_mock, toolkit_http):
    import asyncio

    import httpx

    seen: list[str] = []
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0)
    paypal_mock.get("/t/A").mock(
        side_effect=lambda request: (seen.append("A"), httpx.Response(200, json={}))[1]
    )
    adapter = ToolkitAdapter(gw, client)
    await adapter.prepare()
    try:
        await asyncio.gather(
            gw.call("GET", "/t/A"),
            adapter.run("get_order_details", {"order_id": ORDER_ID}),
            gw.call("GET", "/t/A"),
        )
    finally:
        await gw.stop()
    assert seen == ["A", "A"] and len(toolkit_http["calls"]) == 1
    assert gw.stats["calls"] == 3


def test_parse_toolkit_result():
    assert parse_toolkit_result('{"a": 1}') == {"a": 1}
    assert parse_toolkit_result({"a": 1}) == {"a": 1}
    assert parse_toolkit_result("[1]") == {"result": [1]}
    assert parse_toolkit_result("not json") == {"result": "not json"}
    assert parse_toolkit_result(b'{"b": 2}') == {"b": 2}


# --- live run 2026-10-25 F2: generate-qr-code answers multipart/form-data -----------------------------------

_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
QR_MULTIPART = (
    "--286d91e0-80f6-4be8-9a1a-b73b672748ea\r\n"
    'Content-Disposition: form-data; name="image"\r\n'
    "Content-Type: text/plain\r\n"
    "\r\n"
    f"{_PNG_B64}\r\n"
    "--286d91e0-80f6-4be8-9a1a-b73b672748ea--\r\n"
)


class MultipartResponse(FakeResponse):
    def __init__(self, text: str):
        super().__init__(200, {}, {})
        self.headers = {"content-type": "multipart/form-data; boundary=286d91e0-80f6-4be8-9a1a-b73b672748ea"}
        self.text = text

    def json(self) -> dict:
        raise ValueError("not JSON")


async def test_qr_code_multipart_answer_reaches_the_app(client, gateway, paypal_mock, monkeypatch):
    from app.engine.recovery import _qr_data_url

    posts: list[dict] = []

    def fake_post(url, headers=None, json=None, **kw):
        posts.append({"url": url, "headers": dict(headers or {}), "json": json})
        return MultipartResponse(QR_MULTIPART)

    monkeypatch.setattr("paypal_agent_toolkit.shared.paypal_client.requests.post", fake_post)
    adapter = ToolkitAdapter(gateway, client)
    result = await adapter.run(
        "generate_invoice_qr_code",
        {"invoice_id": "INV2-53GG-K4RR-F3D9-HRMQ", "width": 300, "height": 300},
        request_id="tk:r:c:3:ravi:invqr",
    )
    assert posts[0]["url"].endswith("/v2/invoicing/invoices/INV2-53GG-K4RR-F3D9-HRMQ/generate-qr-code")
    assert posts[0]["headers"]["PayPal-Request-Id"] == "tk:r:c:3:ravi:invqr"
    assert 'name="image"' in result["result"]  # the raw multipart body, not the stock client's {}
    assert _qr_data_url(result) == "data:image/png;base64," + _PNG_B64


def test_qr_parser_formats():
    from app.engine.recovery import _qr_data_url

    assert _qr_data_url({"result": QR_MULTIPART}) == "data:image/png;base64," + _PNG_B64
    assert _qr_data_url(QR_MULTIPART.replace("\r\n", "\n")) == "data:image/png;base64," + _PNG_B64
    assert _qr_data_url({"result": _PNG_B64}) == "data:image/png;base64," + _PNG_B64
    assert _qr_data_url({"qr_code": "data:image/png;base64,AAAA"}) == "data:image/png;base64,AAAA"
    assert _qr_data_url({}) is None and _qr_data_url({"result": "not base64 !!"}) is None
