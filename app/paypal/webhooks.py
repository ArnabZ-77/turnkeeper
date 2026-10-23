"""Webhooks v1: register the listener and verify signatures through PayPal.

Confirmed against the Webhooks Management v1.11 OpenAPI schema (2026-10-22): `POST /v1/notifications/webhooks`
`{"url", "event_types": [{"name"}]}` -> `{"id", "url", "event_types", "links"}`;
`POST /v1/notifications/verify-webhook-signature` `{"auth_algo", "cert_url", "transmission_id", "transmission_sig",
"transmission_time", "webhook_id", "webhook_event"}` -> `{"verification_status": "SUCCESS" | "FAILURE"}`.
The five values come from the `PAYPAL-*` headers PayPal sends with every delivery. Events produced by the
dashboard "simulate" feature cannot be verified (research.md N2a); real deliveries can.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from app.paypal.client import PayPalError
from app.paypal.gateway import Gateway

log = logging.getLogger("turnkeeper.paypal.webhooks")

WEBHOOKS_PATH = "/v1/notifications/webhooks"
VERIFY_PATH = "/v1/notifications/verify-webhook-signature"
EVENT_TYPES_PATH = "/v1/notifications/webhooks-event-types"

WEBHOOK_EVENTS = [
    "VAULT.PAYMENT-TOKEN.CREATED",
    "VAULT.PAYMENT-TOKEN.DELETED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.FAILED",
    "PAYMENT.PAYOUTSBATCH.SUCCESS",
    "INVOICING.INVOICE.PAID",
    "CUSTOMER.DISPUTE.CREATED",
]

# header name (lower-case) -> verify-request field
SIGNATURE_HEADERS = {
    "paypal-auth-algo": "auth_algo",
    "paypal-cert-url": "cert_url",
    "paypal-transmission-id": "transmission_id",
    "paypal-transmission-sig": "transmission_sig",
    "paypal-transmission-time": "transmission_time",
}


async def register_webhook(gw: Gateway, *, url: str, event_types: list[str]) -> dict:
    """POST /v1/notifications/webhooks -> {"id": webhook_id, ...}. One registration per URL; max 10 per app."""
    if not event_types:
        raise ValueError("register_webhook needs at least one event type")
    body = {"url": url, "event_types": [{"name": name} for name in event_types]}
    resp = await gw.call("POST", WEBHOOKS_PATH, json=body)
    return resp.data


async def list_webhooks(gw: Gateway) -> dict:
    resp = await gw.call("GET", WEBHOOKS_PATH)
    return resp.data


async def delete_webhook(gw: Gateway, *, webhook_id: str) -> None:
    await gw.call("DELETE", f"{WEBHOOKS_PATH}/{webhook_id}")


async def list_event_types(gw: Gateway) -> list[str]:
    """Names of every event type the account can subscribe to (used to validate WEBHOOK_EVENTS at registration)."""
    resp = await gw.call("GET", EVENT_TYPES_PATH)
    return [
        str(e["name"]) for e in resp.data.get("event_types") or [] if isinstance(e, dict) and e.get("name")
    ]


def verify_payload(headers: Mapping[str, str], body: bytes, webhook_id: str) -> dict:
    """Build the verify-webhook-signature request body. Raises ValueError when a header is missing or body isn't JSON."""
    if not webhook_id:
        raise ValueError("webhook_id is empty (set PAYPAL_WEBHOOK_ID after scripts/register_webhook.py)")
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    payload: dict = {}
    missing = []
    for header, field in SIGNATURE_HEADERS.items():
        value = lowered.get(header)
        if not value:
            missing.append(header.upper())
        payload[field] = value
    if missing:
        raise ValueError(f"missing webhook signature headers: {', '.join(missing)}")
    try:
        event = json.loads(body.decode("utf-8") if isinstance(body, bytes | bytearray) else body)
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"webhook body is not JSON: {e}") from e
    if not isinstance(event, dict):
        raise ValueError("webhook body must be a JSON object")
    payload["webhook_id"] = webhook_id
    payload["webhook_event"] = event
    return payload


async def verify_signature(gw: Gateway, *, headers: Mapping[str, str], body: bytes, webhook_id: str) -> bool:
    """True only when PayPal answers verification_status == "SUCCESS". Missing headers or a PayPal error -> False."""
    try:
        payload = verify_payload(headers, body, webhook_id)
    except ValueError as e:
        log.warning("webhook signature not verifiable: %s", e)
        return False
    try:
        resp = await gw.call("POST", VERIFY_PATH, json=payload)
    except PayPalError as e:
        log.warning("verify-webhook-signature failed: %s %s debug_id=%s", e.status, e.name, e.debug_id)
        return False
    status = resp.data.get("verification_status")
    if status != "SUCCESS":
        log.warning(
            "webhook signature verification_status=%s for event %s",
            status,
            payload["webhook_event"].get("id"),
        )
    return status == "SUCCESS"


def find_webhook_by_url(listing: dict, url: str) -> dict | None:
    for hook in listing.get("webhooks") or []:
        if isinstance(hook, dict) and hook.get("url") == url:
            return hook
    return None


def event_summary(event: dict) -> dict:
    """Token-free summary of a webhook event for logs and traces (never the full body)."""
    resource = event.get("resource") if isinstance(event.get("resource"), dict) else {}
    return {
        "id": event.get("id"),
        "event_type": event.get("event_type"),
        "resource_type": event.get("resource_type"),
        "resource_id": resource.get("id"),
        "create_time": event.get("create_time"),
    }
