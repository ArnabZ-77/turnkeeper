"""Vault v3 (Payment Method Tokens): setup tokens -> member approval -> payment tokens.

Field names confirmed against the Payment Method Tokens v3.5 OpenAPI schema (2026-10-22):
`payment_source.paypal.{description, usage_type, usage_pattern, customer_type, permit_multiple_payment_tokens,
billing_plan, experience_context.{brand_name, locale, return_url, cancel_url, shipping_preference}}`;
payment tokens are created from `payment_source.token.{id, type: "SETUP_TOKEN"}`.
`PayPal-Request-Id` max length on these endpoints is 108.
"""

from __future__ import annotations

from app.paypal._util import check_len, find_link
from app.paypal.gateway import Gateway

SETUP_TOKENS_PATH = "/v3/vault/setup-tokens"
PAYMENT_TOKENS_PATH = "/v3/vault/payment-tokens"
APPROVE_RELS = ("approve", "payer-action")


async def create_setup_token(
    gw: Gateway,
    *,
    request_id: str,
    description: str,
    billing_plan: dict | None,
    return_url: str,
    cancel_url: str,
    usage_pattern: str = "RECURRING_PREPAID",
    brand_name: str = "Turnkeeper",
    customer_id: str | None = None,
    locale: str | None = None,
) -> dict:
    """POST /v3/vault/setup-tokens for a PayPal wallet source. Returns the response incl. `links` (see approve_link)."""
    paypal: dict = {
        "description": check_len(description, 128, "description"),
        "usage_type": "MERCHANT",
        "usage_pattern": usage_pattern,
        "customer_type": "CONSUMER",
        "permit_multiple_payment_tokens": False,
        "experience_context": {
            "brand_name": check_len(brand_name, 127, "brand_name"),
            "shipping_preference": "NO_SHIPPING",
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }
    if locale:
        paypal["experience_context"]["locale"] = locale
    if billing_plan:
        paypal["billing_plan"] = billing_plan
    body: dict = {"payment_source": {"paypal": paypal}}
    if customer_id:
        body["customer"] = {"id": customer_id}
    resp = await gw.call("POST", SETUP_TOKENS_PATH, json=body, request_id=request_id)
    return resp.data


def approve_link(setup_token_data: dict) -> str | None:
    """The member-facing approval URL (`rel == "approve"`, or "payer-action" on newer responses)."""
    return find_link(setup_token_data, *APPROVE_RELS)


async def get_setup_token(gw: Gateway, *, setup_token_id: str) -> dict:
    """GET /v3/vault/setup-tokens/{id}: status is CREATED | PAYER_ACTION_REQUIRED | APPROVED | VAULTED | TOKENIZED."""
    resp = await gw.call("GET", f"{SETUP_TOKENS_PATH}/{setup_token_id}")
    return resp.data


async def create_payment_token(gw: Gateway, *, setup_token_id: str, request_id: str) -> dict:
    """POST /v3/vault/payment-tokens from an approved setup token -> {"id": vault_id, "customer": {"id": ...}, ...}."""
    body = {"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}}
    resp = await gw.call("POST", PAYMENT_TOKENS_PATH, json=body, request_id=request_id)
    return resp.data


async def get_payment_token(gw: Gateway, *, token_id: str) -> dict:
    resp = await gw.call("GET", f"{PAYMENT_TOKENS_PATH}/{token_id}")
    return resp.data


async def list_payment_tokens(gw: Gateway, *, customer_id: str, page_size: int = 5, page: int = 1) -> dict:
    """GET /v3/vault/payment-tokens?customer_id=... -> {"payment_tokens": [...], "customer": {...}, ...}."""
    params = {"customer_id": customer_id, "page_size": min(max(page_size, 1), 5), "page": max(page, 1), "total_required": "true"}
    resp = await gw.call("GET", PAYMENT_TOKENS_PATH, params=params)
    return resp.data


async def delete_payment_token(gw: Gateway, *, token_id: str) -> None:
    """DELETE /v3/vault/payment-tokens/{id} -> 204 (idempotent on PayPal's side)."""
    await gw.call("DELETE", f"{PAYMENT_TOKENS_PATH}/{token_id}")


def vault_id_of(payment_token_data: dict) -> str:
    vault_id = payment_token_data.get("id")
    if not vault_id:
        raise ValueError("payment token response has no id")
    return str(vault_id)


def customer_id_of(data: dict) -> str | None:
    customer = data.get("customer") or {}
    cid = customer.get("id") if isinstance(customer, dict) else None
    return str(cid) if cid else None
