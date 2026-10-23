"""PayPalClient: token caching, 401 refresh, 429/5xx retries, 4xx no-retry, headers, 204."""

from __future__ import annotations

import httpx
import pytest

from app.paypal.client import PayPalError
from tests.paypal.conftest import token_response

DECLINE_BODY = {
    "name": "UNPROCESSABLE_ENTITY",
    "message": "The requested action could not be performed, semantically incorrect, or failed business validation.",
    "debug_id": "d3b0e5f1a2c34",
    "details": [
        {"issue": "INSTRUMENT_DECLINED", "description": "The instrument presented was either declined by the processor or bank, or it can't be used for this payment."}
    ],
    "links": [],
}


async def test_token_is_cached_across_requests(client, paypal_mock):
    route = paypal_mock.get("/v2/checkout/orders/ABC").mock(return_value=httpx.Response(200, json={"id": "ABC"}))
    first = await client.request("GET", "/v2/checkout/orders/ABC")
    second = await client.request("GET", "/v2/checkout/orders/ABC")
    assert first.status == 200 and second.data == {"id": "ABC"}
    assert paypal_mock["token"].call_count == 1
    assert route.call_count == 2
    headers = route.calls.last.request.headers
    assert headers["Authorization"] == "Bearer tok-1"
    assert headers["Content-Type"] == "application/json"
    assert headers["Prefer"] == "return=representation"
    assert "PayPal-Request-Id" not in headers


async def test_token_fetch_uses_basic_auth_and_client_credentials(client, paypal_mock):
    paypal_mock.get("/v1/reporting/balances").mock(return_value=httpx.Response(200, json={"balances": []}))
    await client.request("GET", "/v1/reporting/balances", params={"currency_code": "USD"})
    token_req = paypal_mock["token"].calls.last.request
    assert token_req.headers["Authorization"].startswith("Basic ")
    assert b"grant_type=client_credentials" in token_req.content
    assert client.cached_token() == "tok-1"


async def test_token_refreshes_when_about_to_expire(client, paypal_mock):
    paypal_mock["token"].side_effect = [token_response("tok-1"), token_response("tok-2")]
    route = paypal_mock.get("/v2/checkout/orders/X").mock(return_value=httpx.Response(200, json={}))
    await client.request("GET", "/v2/checkout/orders/X")
    client._token_expires_at = client._clock() - 1  # pretend < 300 s are left
    await client.request("GET", "/v2/checkout/orders/X")
    assert paypal_mock["token"].call_count == 2
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-2"


async def test_short_lived_token_is_not_cached_as_expired(client, paypal_mock):
    paypal_mock["token"].side_effect = [token_response("tok-s", expires_in=100)]
    await client.get_token()
    assert client.cached_token() == "tok-s"


async def test_401_refreshes_token_once_and_retries(client, paypal_mock):
    paypal_mock["token"].side_effect = [token_response("tok-1"), token_response("tok-2")]
    route = paypal_mock.get("/v2/checkout/orders/X").mock(
        side_effect=[httpx.Response(401, json={"error": "invalid_token"}), httpx.Response(200, json={"id": "X"})]
    )
    resp = await client.request("GET", "/v2/checkout/orders/X")
    assert resp.data == {"id": "X"}
    assert route.call_count == 2
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok-1"
    assert route.calls[1].request.headers["Authorization"] == "Bearer tok-2"
    assert paypal_mock["token"].call_count == 2
    assert client.sleeps == []


async def test_persistent_401_raises_after_one_refresh(client, paypal_mock):
    route = paypal_mock.get("/v2/checkout/orders/X").mock(
        return_value=httpx.Response(401, json={"error": "invalid_token", "error_description": "Token signature failed"})
    )
    with pytest.raises(PayPalError) as exc:
        await client.request("GET", "/v2/checkout/orders/X")
    assert exc.value.status == 401
    assert exc.value.name == "invalid_token"
    assert "Token signature failed" in exc.value.message
    assert route.call_count == 2


async def test_429_is_retried_with_backoff(client, paypal_mock):
    route = paypal_mock.post("/v1/payments/payouts").mock(
        side_effect=[
            httpx.Response(429, json={"name": "RATE_LIMIT_REACHED", "message": "Too many requests"}),
            httpx.Response(429, json={"name": "RATE_LIMIT_REACHED", "message": "Too many requests"}),
            httpx.Response(201, json={"batch_header": {"payout_batch_id": "B1"}}),
        ]
    )
    resp = await client.request("POST", "/v1/payments/payouts", json={"x": 1}, request_id="tk:run:c:3:r1")
    assert resp.status == 201
    assert route.call_count == 3
    assert client.sleeps == [1.0, 2.0]
    assert all(c.request.headers["PayPal-Request-Id"] == "tk:run:c:3:r1" for c in route.calls)


async def test_5xx_exhausts_three_retries_then_raises(client, paypal_mock):
    route = paypal_mock.get("/v2/checkout/orders/X").mock(
        side_effect=[
            httpx.Response(500, json={"name": "INTERNAL_SERVER_ERROR", "message": "boom", "debug_id": "1"}),
            httpx.Response(502, text="bad gateway"),
            httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE"}),
            httpx.Response(504, text=""),
        ]
    )
    with pytest.raises(PayPalError) as exc:
        await client.request("GET", "/v2/checkout/orders/X")
    assert exc.value.status == 504
    assert exc.value.retryable
    assert route.call_count == 4
    assert client.sleeps == [1.0, 2.0, 4.0]


async def test_business_4xx_is_never_retried_and_exposes_issue_codes(client, paypal_mock):
    route = paypal_mock.post("/v2/checkout/orders").mock(return_value=httpx.Response(422, json=DECLINE_BODY))
    with pytest.raises(PayPalError) as exc:
        await client.request("POST", "/v2/checkout/orders", json={"intent": "CAPTURE"}, request_id="tk:r:c:3:ravi:a1")
    err = exc.value
    assert route.call_count == 1
    assert client.sleeps == []
    assert err.status == 422
    assert err.name == "UNPROCESSABLE_ENTITY"
    assert err.issue_codes == ["INSTRUMENT_DECLINED"]
    assert err.has_issue("INSTRUMENT_DECLINED")
    assert err.debug_id == "d3b0e5f1a2c34"
    assert err.details[0]["issue"] == "INSTRUMENT_DECLINED"
    assert err.raw == DECLINE_BODY
    assert not err.retryable
    assert "UNPROCESSABLE_ENTITY" in str(err) and "INSTRUMENT_DECLINED" in str(err)
    assert err.to_dict() == {
        "status": 422,
        "name": "UNPROCESSABLE_ENTITY",
        "message": DECLINE_BODY["message"],
        "debug_id": "d3b0e5f1a2c34",
        "issues": ["INSTRUMENT_DECLINED"],
    }


async def test_404_without_json_body(client, paypal_mock):
    paypal_mock.get("/v2/checkout/orders/NOPE").mock(return_value=httpx.Response(404, text="not found"))
    with pytest.raises(PayPalError) as exc:
        await client.request("GET", "/v2/checkout/orders/NOPE")
    assert exc.value.status == 404
    assert exc.value.name == "HTTP_404"
    assert exc.value.message == "not found"
    assert exc.value.issue_codes == []


async def test_request_id_and_custom_headers_are_sent(client, paypal_mock):
    route = paypal_mock.post("/v2/checkout/orders").mock(return_value=httpx.Response(201, json={"id": "O1"}))
    await client.request(
        "POST",
        "/v2/checkout/orders",
        json={"intent": "CAPTURE"},
        headers={"PayPal-Mock-Response": '{"mock_application_codes":"INSTRUMENT_DECLINED"}'},
        request_id="tk:abcdefgh:c1x2y3:3:ravi:a1",
    )
    headers = route.calls.last.request.headers
    assert headers["PayPal-Request-Id"] == "tk:abcdefgh:c1x2y3:3:ravi:a1"
    assert headers["PayPal-Mock-Response"] == '{"mock_application_codes":"INSTRUMENT_DECLINED"}'
    assert headers["Authorization"] == "Bearer tok-1"


async def test_204_yields_empty_data(client, paypal_mock):
    paypal_mock.delete("/v3/vault/payment-tokens/8kk8451t").mock(return_value=httpx.Response(204))
    resp = await client.request("DELETE", "/v3/vault/payment-tokens/8kk8451t")
    assert resp.status == 204
    assert resp.data == {}


async def test_non_object_json_is_wrapped(client, paypal_mock):
    paypal_mock.get("/v1/weird").mock(return_value=httpx.Response(200, json=[1, 2]))
    resp = await client.request("GET", "/v1/weird")
    assert resp.data == {"_value": [1, 2]}


async def test_token_endpoint_failure_raises_paypal_error(client, paypal_mock):
    paypal_mock["token"].mock(
        return_value=httpx.Response(401, json={"error": "invalid_client", "error_description": "Client Authentication failed"})
    )
    with pytest.raises(PayPalError) as exc:
        await client.get_token()
    assert exc.value.status == 401
    assert exc.value.name == "invalid_client"
    assert exc.value.path == "/v1/oauth2/token"


async def test_token_endpoint_5xx_is_retried(client, paypal_mock):
    paypal_mock["token"].side_effect = [httpx.Response(503, text=""), token_response("tok-9")]
    assert await client.get_token() == "tok-9"
    assert client.sleeps == [1.0]
    assert client.token_fetches == 2


async def test_transport_error_retried_only_when_safe(client, paypal_mock):
    get_route = paypal_mock.get("/v2/checkout/orders/X").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"id": "X"})]
    )
    resp = await client.request("GET", "/v2/checkout/orders/X")
    assert resp.data == {"id": "X"} and get_route.call_count == 2 and client.sleeps == [1.0]

    post_route = paypal_mock.post("/v2/checkout/orders").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PayPalError) as exc:
        await client.request("POST", "/v2/checkout/orders", json={})  # no request id -> never repeat a money POST
    assert exc.value.status == 0 and exc.value.name == "TRANSPORT_ERROR"
    assert post_route.call_count == 1


async def test_concurrent_callers_share_one_token_fetch(client, paypal_mock):
    import asyncio

    paypal_mock.get("/v2/checkout/orders/X").mock(return_value=httpx.Response(200, json={}))
    await asyncio.gather(*(client.request("GET", "/v2/checkout/orders/X") for _ in range(5)))
    assert paypal_mock["token"].call_count == 1
