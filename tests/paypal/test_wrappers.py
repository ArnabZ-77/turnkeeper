"""Typed wrappers: request shapes on the wire, response parsing, mock-decline header, verify payload shape."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from app.paypal import balances, orders, payouts, refunds, vault, webhooks
from app.paypal.client import PayPalError

COMPLETED_ORDER = {
    "id": "5O190127TN364715T",
    "status": "COMPLETED",
    "intent": "CAPTURE",
    "purchase_units": [
        {
            "reference_id": "c1x2y3:3:asha",
            "payments": {
                "captures": [
                    {
                        "id": "3C679366HH908993F",
                        "status": "COMPLETED",
                        "amount": {"currency_code": "USD", "value": "200.00"},
                        "final_capture": True,
                        "seller_receivable_breakdown": {
                            "gross_amount": {"currency_code": "USD", "value": "200.00"},
                            "paypal_fee": {"currency_code": "USD", "value": "7.47"},
                            "net_amount": {"currency_code": "USD", "value": "192.53"},
                        },
                    }
                ]
            },
        }
    ],
    "links": [
        {
            "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/5O190127TN364715T",
            "rel": "self",
            "method": "GET",
        }
    ],
}

DECLINE_422 = {
    "name": "UNPROCESSABLE_ENTITY",
    "message": "The requested action could not be performed, semantically incorrect, or failed business validation.",
    "debug_id": "ab12",
    "details": [{"issue": "INSTRUMENT_DECLINED", "description": "The instrument presented was declined."}],
}


def body_of(route) -> dict:
    return json.loads(route.calls.last.request.content)


# ------------------------------------------------------------------------------------------------ orders


async def test_charge_vaulted_sends_capture_intent_and_vault_id(gateway, paypal_mock):
    route = paypal_mock.post("/v2/checkout/orders").mock(
        return_value=httpx.Response(201, json=COMPLETED_ORDER)
    )
    data = await orders.charge_vaulted(
        gateway,
        request_id="tk:abcdefgh:c1x2y3:3:asha:a1",
        vault_id="8kk8451t",
        amount=Decimal("200"),
        reference_id="c1x2y3:3:asha",
        custom_id="abcdefgh:1a2b3c4d",
        description="Round 3 contribution",
    )
    assert data["status"] == "COMPLETED"
    body = body_of(route)
    assert body == {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {"currency_code": "USD", "value": "200.00"},
                "reference_id": "c1x2y3:3:asha",
                "custom_id": "abcdefgh:1a2b3c4d",
                "description": "Round 3 contribution",
            }
        ],
        "payment_source": {"paypal": {"vault_id": "8kk8451t"}},
    }
    headers = route.calls.last.request.headers
    assert headers["PayPal-Request-Id"] == "tk:abcdefgh:c1x2y3:3:asha:a1"
    assert "PayPal-Mock-Response" not in headers


async def test_charge_vaulted_mock_decline_header_and_error_mapping(gateway, paypal_mock):
    route = paypal_mock.post("/v2/checkout/orders").mock(return_value=httpx.Response(422, json=DECLINE_422))
    with pytest.raises(PayPalError) as exc:
        await orders.charge_vaulted(
            gateway,
            request_id="tk:abcdefgh:c1x2y3:3:ravi:a1",
            vault_id="9ravi",
            amount=Decimal("200.00"),
            reference_id="c1x2y3:3:ravi",
            custom_id="abcdefgh:1a2b3c4d",
            description="Round 3 contribution",
            mock_decline=True,
        )
    header = route.calls.last.request.headers["PayPal-Mock-Response"]
    assert json.loads(header) == {"mock_application_codes": "INSTRUMENT_DECLINED"}
    assert header == '{"mock_application_codes":"INSTRUMENT_DECLINED"}'
    assert route.call_count == 1  # never retried
    err = exc.value
    assert orders.is_decline(err)
    assert orders.decline_code(err) == "INSTRUMENT_DECLINED"
    assert not orders.is_decline(PayPalError(500, {"name": "INTERNAL_SERVER_ERROR"}))
    assert orders.decline_code(PayPalError(503, {"name": "SERVICE_UNAVAILABLE"})) is None


async def test_charge_vaulted_rejects_bad_amounts_before_calling(gateway, paypal_mock):
    route = paypal_mock.post("/v2/checkout/orders").mock(
        return_value=httpx.Response(201, json=COMPLETED_ORDER)
    )
    for bad in (Decimal("0"), Decimal("-1")):
        with pytest.raises(ValueError):
            await orders.charge_vaulted(
                gateway,
                request_id="tk:a:b:1:m:a1",
                vault_id="v",
                amount=bad,
                reference_id="r",
                custom_id="c",
                description="d",
            )
    with pytest.raises(TypeError):
        await orders.charge_vaulted(
            gateway,
            request_id="tk:a:b:1:m:a1",
            vault_id="v",
            amount=200.0,
            reference_id="r",
            custom_id="c",
            description="d",
        )
    assert route.call_count == 0


def test_capture_summary_parses_seller_receivable_breakdown():
    summary = orders.capture_summary(COMPLETED_ORDER)
    assert summary.order_id == "5O190127TN364715T"
    assert summary.capture_id == "3C679366HH908993F"
    assert summary.status == "COMPLETED" and summary.completed
    assert summary.gross == Decimal("200.00")
    assert summary.fee == Decimal("7.47")
    assert summary.net == Decimal("192.53")
    assert summary.currency == "USD"
    assert summary.gross - summary.fee == summary.net


def test_capture_summary_without_breakdown_or_captures():
    order = {
        "id": "X1",
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": "C1",
                            "status": "PENDING",
                            "amount": {"currency_code": "USD", "value": "200.00"},
                        }
                    ]
                }
            }
        ],
    }
    s = orders.capture_summary(order)
    assert (s.capture_id, s.status, s.gross, s.fee, s.net) == ("C1", "PENDING", Decimal("200.00"), None, None)
    assert not s.completed
    empty = orders.capture_summary({"id": "X2", "status": "CREATED"})
    assert (empty.order_id, empty.capture_id, empty.status, empty.gross) == ("X2", None, "CREATED", None)
    assert orders.capture_summary({}).status == "UNKNOWN"


async def test_get_order_and_capture_order(gateway, paypal_mock):
    get_route = paypal_mock.get("/v2/checkout/orders/5O190127TN364715T").mock(
        return_value=httpx.Response(200, json=COMPLETED_ORDER)
    )
    assert (await orders.get_order(gateway, order_id="5O190127TN364715T"))["id"] == "5O190127TN364715T"
    assert get_route.called
    cap_route = paypal_mock.post("/v2/checkout/orders/5O190127TN364715T/capture").mock(
        return_value=httpx.Response(201, json=COMPLETED_ORDER)
    )
    data = await orders.capture_order(
        gateway, order_id="5O190127TN364715T", request_id="tk:abcdefgh:c1x2y3:setup:asha"
    )
    assert data["status"] == "COMPLETED"
    assert cap_route.calls.last.request.content == b""
    assert cap_route.calls.last.request.headers["PayPal-Request-Id"] == "tk:abcdefgh:c1x2y3:setup:asha"


async def test_vault_with_purchase_order_shape_and_approve_link(gateway, paypal_mock):
    created = {
        "id": "7WH94211LK834082R",
        "status": "PAYER_ACTION_REQUIRED",
        "links": [
            {
                "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/7WH94211LK834082R",
                "rel": "self",
                "method": "GET",
            },
            {
                "href": "https://www.sandbox.paypal.com/checkoutnow?token=7WH94211LK834082R",
                "rel": "payer-action",
                "method": "GET",
            },
        ],
    }
    route = paypal_mock.post("/v2/checkout/orders").mock(return_value=httpx.Response(201, json=created))
    data = await orders.create_vault_with_purchase_order(
        gateway,
        request_id="tk:abcdefgh:c1x2y3:setup:asha",
        amount=Decimal("200.00"),
        return_url="https://example.test/join/t/return",
        cancel_url="https://example.test/join/t/cancel",
        reference_id="c1x2y3:1:asha",
        description="Round 1 contribution",
    )
    assert orders.approve_link(data) == "https://www.sandbox.paypal.com/checkoutnow?token=7WH94211LK834082R"
    body = body_of(route)
    paypal = body["payment_source"]["paypal"]
    assert paypal["attributes"]["vault"] == {
        "store_in_vault": "ON_SUCCESS",
        "usage_type": "MERCHANT",
        "usage_pattern": "RECURRING_PREPAID",
        "customer_type": "CONSUMER",
        "permit_multiple_payment_tokens": False,
    }
    assert paypal["experience_context"]["return_url"] == "https://example.test/join/t/return"
    assert paypal["experience_context"]["user_action"] == "PAY_NOW"
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "200.00"}
    assert orders.approve_link({"links": []}) is None


# ------------------------------------------------------------------------------------------------ vault


async def test_create_setup_token_shape_and_approve_link(gateway, paypal_mock):
    created = {
        "id": "5C991763VB2781612",
        "status": "PAYER_ACTION_REQUIRED",
        "links": [
            {
                "href": "https://api-m.sandbox.paypal.com/v3/vault/setup-tokens/5C991763VB2781612",
                "rel": "self",
                "method": "GET",
            },
            {
                "href": "https://www.sandbox.paypal.com/agreements/approve?approval_session_id=5C991763VB2781612",
                "rel": "approve",
                "method": "GET",
            },
        ],
    }
    route = paypal_mock.post("/v3/vault/setup-tokens").mock(return_value=httpx.Response(201, json=created))
    plan = {
        "name": "Temple Circle A",
        "billing_cycles": [{"tenure_type": "REGULAR", "sequence": 1, "total_cycles": 6}],
    }
    data = await vault.create_setup_token(
        gateway,
        request_id="tk:abcdefgh:c1x2y3:setup:asha",
        description="Turnkeeper - Temple Circle A - $200 monthly x 6",
        billing_plan=plan,
        return_url="https://example.test/join/t/return",
        cancel_url="https://example.test/join/t/cancel",
    )
    assert (
        vault.approve_link(data)
        == "https://www.sandbox.paypal.com/agreements/approve?approval_session_id=5C991763VB2781612"
    )
    body = body_of(route)
    paypal = body["payment_source"]["paypal"]
    assert paypal["usage_type"] == "MERCHANT"
    assert paypal["usage_pattern"] == "RECURRING_PREPAID"
    assert paypal["customer_type"] == "CONSUMER"
    assert paypal["permit_multiple_payment_tokens"] is False
    assert paypal["billing_plan"] == plan
    assert paypal["experience_context"] == {
        "brand_name": "Turnkeeper",
        "shipping_preference": "NO_SHIPPING",
        "return_url": "https://example.test/join/t/return",
        "cancel_url": "https://example.test/join/t/cancel",
    }
    assert "customer" not in body
    assert route.calls.last.request.headers["PayPal-Request-Id"] == "tk:abcdefgh:c1x2y3:setup:asha"


async def test_setup_token_without_plan_and_with_customer(gateway, paypal_mock):
    route = paypal_mock.post("/v3/vault/setup-tokens").mock(
        return_value=httpx.Response(201, json={"id": "S1", "links": []})
    )
    data = await vault.create_setup_token(
        gateway,
        request_id="tk:a:b:setup:m",
        description="d",
        billing_plan=None,
        return_url="https://r",
        cancel_url="https://c",
        usage_pattern="UNSCHEDULED_PREPAID",
        customer_id="cust-1",
        locale="en-US",
    )
    body = body_of(route)
    assert "billing_plan" not in body["payment_source"]["paypal"]
    assert body["payment_source"]["paypal"]["usage_pattern"] == "UNSCHEDULED_PREPAID"
    assert body["payment_source"]["paypal"]["experience_context"]["locale"] == "en-US"
    assert body["customer"] == {"id": "cust-1"}
    assert vault.approve_link(data) is None


async def test_payment_token_lifecycle(gateway, paypal_mock):
    create = paypal_mock.post("/v3/vault/payment-tokens").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "8kk8451t",
                "customer": {"id": "customer_4029352050"},
                "payment_source": {"paypal": {}},
            },
        )
    )
    data = await vault.create_payment_token(
        gateway, setup_token_id="5C991763VB2781612", request_id="tk:a:b:setup:m"
    )
    assert body_of(create) == {
        "payment_source": {"token": {"id": "5C991763VB2781612", "type": "SETUP_TOKEN"}}
    }
    assert vault.vault_id_of(data) == "8kk8451t"
    assert vault.customer_id_of(data) == "customer_4029352050"

    listing = paypal_mock.get("/v3/vault/payment-tokens").mock(
        return_value=httpx.Response(
            200, json={"payment_tokens": [{"id": "8kk8451t"}], "customer": {"id": "customer_4029352050"}}
        )
    )
    tokens = await vault.list_payment_tokens(gateway, customer_id="customer_4029352050")
    assert tokens["payment_tokens"][0]["id"] == "8kk8451t"
    params = dict(listing.calls.last.request.url.params)
    assert params["customer_id"] == "customer_4029352050" and params["page_size"] == "5"

    delete = paypal_mock.delete("/v3/vault/payment-tokens/8kk8451t").mock(return_value=httpx.Response(204))
    assert await vault.delete_payment_token(gateway, token_id="8kk8451t") is None
    assert delete.called

    get = paypal_mock.get("/v3/vault/setup-tokens/5C991763VB2781612").mock(
        return_value=httpx.Response(200, json={"status": "APPROVED"})
    )
    assert (await vault.get_setup_token(gateway, setup_token_id="5C991763VB2781612"))["status"] == "APPROVED"
    assert get.called
    with pytest.raises(ValueError):
        vault.vault_id_of({})


# ------------------------------------------------------------------------------------------------ payouts


def test_payout_item_wire_shape_and_validation():
    item = payouts.PayoutItem(
        receiver="meena@example.com",
        amount=Decimal("1155.18"),
        note="Temple Circle A - Round 3 pot",
        sender_item_id="abcdefghc1x2y33meena",
    )
    assert item.to_wire() == {
        "recipient_type": "EMAIL",
        "receiver": "meena@example.com",
        "recipient_wallet": "PAYPAL",
        "amount": {"value": "1155.18", "currency": "USD"},
        "sender_item_id": "abcdefghc1x2y33meena",
        "note": "Temple Circle A - Round 3 pot",
    }
    venmo = payouts.PayoutItem(
        recipient_type="PHONE",
        receiver="+15551234567",
        wallet="VENMO",
        amount=Decimal("5"),
        sender_item_id="x",
    )
    assert venmo.to_wire()["recipient_wallet"] == "VENMO" and venmo.to_wire()["amount"]["value"] == "5.00"
    assert "note" not in venmo.to_wire()
    with pytest.raises(ValidationError):
        payouts.PayoutItem(receiver="a@b", amount=Decimal("0"), sender_item_id="x")
    with pytest.raises(ValidationError):
        payouts.PayoutItem(receiver="a@b", amount=Decimal("1"), sender_item_id="x" * 64)
    with pytest.raises(ValidationError):
        payouts.PayoutItem(receiver="a@b", wallet="CASHAPP", amount=Decimal("1"), sender_item_id="x")


async def test_create_payout_body_and_idempotency(gateway, paypal_mock):
    created = {
        "batch_header": {
            "payout_batch_id": "5MX67483MC6894821",
            "batch_status": "PENDING",
            "sender_batch_header": {"sender_batch_id": "tk:abcdefgh:c1x2y3:3:r1"},
        }
    }
    route = paypal_mock.post("/v1/payments/payouts").mock(return_value=httpx.Response(201, json=created))
    item = payouts.PayoutItem(
        receiver="meena@example.com", amount=Decimal("1155.18"), sender_item_id="abcdefghc1x2y33meena"
    )
    data = await payouts.create_payout(
        gateway,
        sender_batch_id="tk:abcdefgh:c1x2y3:3:r1",
        items=[item],
        email_subject="Your circle pot is here",
    )
    assert payouts.batch_id_of(data) == "5MX67483MC6894821"
    assert payouts.batch_status(data) == "PENDING"
    body = body_of(route)
    assert body["sender_batch_header"] == {
        "sender_batch_id": "tk:abcdefgh:c1x2y3:3:r1",
        "email_subject": "Your circle pot is here",
    }
    assert body["items"] == [item.to_wire()]
    assert route.calls.last.request.headers["PayPal-Request-Id"] == "tk:abcdefgh:c1x2y3:3:r1"
    with pytest.raises(ValueError):
        await payouts.create_payout(gateway, sender_batch_id="x" * 31, items=[item], email_subject="s")
    with pytest.raises(ValueError):
        await payouts.create_payout(gateway, sender_batch_id="tk:1", items=[], email_subject="s")


async def test_payout_status_helpers(gateway, paypal_mock):
    batch = {
        "batch_header": {
            "payout_batch_id": "B1",
            "batch_status": "SUCCESS",
            "fees": {"currency": "USD", "value": "0.25"},
        },
        "items": [
            {
                "payout_item_id": "I1",
                "transaction_status": "UNCLAIMED",
                "payout_item_fee": {"currency": "USD", "value": "0.25"},
                "payout_item": {"receiver": "nobody@example.com"},
            }
        ],
    }
    paypal_mock.get("/v1/payments/payouts/B1").mock(return_value=httpx.Response(200, json=batch))
    data = await payouts.get_payout_batch(gateway, batch_id="B1")
    item = payouts.first_item(data)
    assert payouts.item_status(item) == "UNCLAIMED"
    assert payouts.item_id_of(item) == "I1"
    assert payouts.item_fee(item) == Decimal("0.25")
    assert payouts.batch_fees(data) == Decimal("0.25")
    assert payouts.first_item({"items": []}) is None and payouts.item_status(None) == "UNKNOWN"

    paypal_mock.get("/v1/payments/payouts-item/I1").mock(
        return_value=httpx.Response(200, json=batch["items"][0])
    )
    assert payouts.item_status(await payouts.get_payout_item(gateway, item_id="I1")) == "UNCLAIMED"
    cancel = paypal_mock.post("/v1/payments/payouts-item/I1/cancel").mock(
        return_value=httpx.Response(200, json={**batch["items"][0], "transaction_status": "RETURNED"})
    )
    assert payouts.item_status(await payouts.cancel_payout_item(gateway, item_id="I1")) == "RETURNED"
    assert cancel.calls.last.request.content == b""


# ------------------------------------------------------------------------------------------------ balances


async def test_balances_request_and_available_amount(gateway, paypal_mock):
    payload = {
        "balances": [
            {
                "currency": "USD",
                "primary": True,
                "total_balance": {"currency_code": "USD", "value": "5000.00"},
                "available_balance": {"currency_code": "USD", "value": "4321.09"},
                "withheld_balance": {"currency_code": "USD", "value": "0.00"},
            }
        ],
        "account_id": "X5QHQ3P4V3EGW",
        "as_of_time": "2026-10-22T10:00:00Z",
        "last_refresh_time": "2026-10-22T09:59:00Z",
    }
    route = paypal_mock.get("/v1/reporting/balances").mock(return_value=httpx.Response(200, json=payload))
    data = await balances.get_balances(gateway)
    assert dict(route.calls.last.request.url.params) == {"currency_code": "USD"}
    assert balances.available_amount(data) == Decimal("4321.09")
    assert balances.total_amount(data) == Decimal("5000.00")
    assert balances.available_amount(data, "EUR") is None
    assert balances.available_amount({}) is None
    assert balances.last_refresh_time(data) == "2026-10-22T09:59:00Z"


# ------------------------------------------------------------------------------------------------ refunds


async def test_refund_capture_full_and_partial(gateway, paypal_mock):
    route = paypal_mock.post("/v2/payments/captures/3C679366HH908993F/refund").mock(
        return_value=httpx.Response(201, json={"id": "R1", "status": "COMPLETED"})
    )
    data = await refunds.refund_capture(
        gateway, capture_id="3C679366HH908993F", request_id="tk:abcdefgh:c1x2y3:3:asha:a1"
    )
    assert refunds.refund_status(data) == "COMPLETED"
    assert body_of(route) == {}
    await refunds.refund_capture(
        gateway, capture_id="3C679366HH908993F", request_id="tk:x", amount=Decimal("1"), note="canary"
    )
    assert body_of(route) == {"amount": {"currency_code": "USD", "value": "1.00"}, "note_to_payer": "canary"}
    assert route.calls.last.request.headers["PayPal-Request-Id"] == "tk:x"


# ------------------------------------------------------------------------------------------------ webhooks

EVENT_HEADERS = {
    "Paypal-Auth-Algo": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-360caa42-fca2a594-1d93a270",
    "paypal-transmission-id": "69cd13f0-d67a-11e5-baa3-778b53f4ae55",
    "PAYPAL-TRANSMISSION-SIG": "lmI95Jx3Y9nhR5SJWlHVIWpg4AgFk7n9bCHSRxbrd8A9S1uRy7v",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-22T10:00:00Z",
    "Content-Type": "application/json",
}
EVENT_BODY = b'{"id":"WH-2WR32451HC0233532-67976317FL4543714","event_type":"PAYMENT.CAPTURE.COMPLETED","resource":{"id":"3C679366HH908993F","amount":{"value":"200.00"}}}'


async def test_verify_signature_payload_shape(gateway, paypal_mock):
    route = paypal_mock.post("/v1/notifications/verify-webhook-signature").mock(
        return_value=httpx.Response(200, json={"verification_status": "SUCCESS"})
    )
    ok = await webhooks.verify_signature(
        gateway, headers=EVENT_HEADERS, body=EVENT_BODY, webhook_id="WH-TEST"
    )
    assert ok is True
    assert body_of(route) == {
        "auth_algo": "SHA256withRSA",
        "cert_url": EVENT_HEADERS["PAYPAL-CERT-URL"],
        "transmission_id": "69cd13f0-d67a-11e5-baa3-778b53f4ae55",
        "transmission_sig": EVENT_HEADERS["PAYPAL-TRANSMISSION-SIG"],
        "transmission_time": "2026-10-22T10:00:00Z",
        "webhook_id": "WH-TEST",
        "webhook_event": json.loads(EVENT_BODY),
    }


async def test_verify_signature_failure_paths(gateway, paypal_mock):
    route = paypal_mock.post("/v1/notifications/verify-webhook-signature").mock(
        return_value=httpx.Response(200, json={"verification_status": "FAILURE"})
    )
    assert (
        await webhooks.verify_signature(gateway, headers=EVENT_HEADERS, body=EVENT_BODY, webhook_id="WH-TEST")
        is False
    )
    assert route.call_count == 1
    incomplete = {k: v for k, v in EVENT_HEADERS.items() if k != "PAYPAL-TRANSMISSION-SIG"}
    assert (
        await webhooks.verify_signature(gateway, headers=incomplete, body=EVENT_BODY, webhook_id="WH-TEST")
        is False
    )
    assert (
        await webhooks.verify_signature(
            gateway, headers=EVENT_HEADERS, body=b"not json", webhook_id="WH-TEST"
        )
        is False
    )
    assert (
        await webhooks.verify_signature(gateway, headers=EVENT_HEADERS, body=EVENT_BODY, webhook_id="")
        is False
    )
    assert route.call_count == 1  # nothing was sent for the three unverifiable cases
    route.mock(return_value=httpx.Response(400, json={"name": "VALIDATION_ERROR"}))
    assert (
        await webhooks.verify_signature(gateway, headers=EVENT_HEADERS, body=EVENT_BODY, webhook_id="WH-TEST")
        is False
    )
    with pytest.raises(ValueError, match="missing webhook signature headers: PAYPAL-TRANSMISSION-SIG"):
        webhooks.verify_payload(incomplete, EVENT_BODY, "WH-TEST")


async def test_register_and_list_webhooks(gateway, paypal_mock):
    created = {
        "id": "0EH40505U7160970P",
        "url": "https://turnkeeper.example/webhooks/paypal",
        "event_types": [{"name": n} for n in webhooks.WEBHOOK_EVENTS],
    }
    route = paypal_mock.post("/v1/notifications/webhooks").mock(
        return_value=httpx.Response(201, json=created)
    )
    data = await webhooks.register_webhook(
        gateway, url="https://turnkeeper.example/webhooks/paypal", event_types=webhooks.WEBHOOK_EVENTS
    )
    assert data["id"] == "0EH40505U7160970P"
    assert body_of(route) == {
        "url": "https://turnkeeper.example/webhooks/paypal",
        "event_types": [{"name": n} for n in webhooks.WEBHOOK_EVENTS],
    }
    assert len(webhooks.WEBHOOK_EVENTS) == 10 and "PAYMENT.CAPTURE.COMPLETED" in webhooks.WEBHOOK_EVENTS
    paypal_mock.get("/v1/notifications/webhooks").mock(
        return_value=httpx.Response(200, json={"webhooks": [created]})
    )
    listing = await webhooks.list_webhooks(gateway)
    assert (
        webhooks.find_webhook_by_url(listing, "https://turnkeeper.example/webhooks/paypal")["id"]
        == "0EH40505U7160970P"
    )
    assert webhooks.find_webhook_by_url(listing, "https://other") is None
    paypal_mock.get("/v1/notifications/webhooks-event-types").mock(
        return_value=httpx.Response(
            200,
            json={
                "event_types": [
                    {"name": "PAYMENT.CAPTURE.COMPLETED"},
                    {"name": "VAULT.PAYMENT-TOKEN.CREATED"},
                ]
            },
        )
    )
    assert await webhooks.list_event_types(gateway) == [
        "PAYMENT.CAPTURE.COMPLETED",
        "VAULT.PAYMENT-TOKEN.CREATED",
    ]
    assert webhooks.event_summary(json.loads(EVENT_BODY)) == {
        "id": "WH-2WR32451HC0233532-67976317FL4543714",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource_type": None,
        "resource_id": "3C679366HH908993F",
        "create_time": None,
    }
    with pytest.raises(ValueError):
        await webhooks.register_webhook(gateway, url="https://x", event_types=[])
