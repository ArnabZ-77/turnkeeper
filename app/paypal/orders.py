"""Orders v2: merchant-initiated charges on a vaulted PayPal wallet, plus the F18 vault-with-purchase fallback.

Field names confirmed against the Orders v2.36 OpenAPI schema (2026-10-22): `intent`, `purchase_units[].{reference_id,
custom_id, description, amount.{currency_code, value}}`, `payment_source.paypal.vault_id`,
`payment_source.paypal.attributes.vault.{store_in_vault, usage_type, usage_pattern, customer_type,
permit_multiple_payment_tokens}`, `payment_source.paypal.experience_context.{brand_name, return_url, cancel_url,
shipping_preference, user_action}`; captures carry `seller_receivable_breakdown.{gross_amount, paypal_fee,
net_amount}`. Negative testing header (developer.paypal.com/tools/sandbox/negative-testing/request-headers):
`PayPal-Mock-Response: {"mock_application_codes": "INSTRUMENT_DECLINED"}`, supported on Create order.
"""

from __future__ import annotations

import json
from decimal import Decimal

from pydantic import BaseModel

from app.paypal._util import check_len, find_link, money_str, to_decimal
from app.paypal.client import PayPalError
from app.paypal.gateway import Gateway

ORDERS_PATH = "/v2/checkout/orders"
MOCK_RESPONSE_HEADER = "PayPal-Mock-Response"
DEFAULT_MOCK_CODE = "INSTRUMENT_DECLINED"
APPROVE_RELS = ("approve", "payer-action")
# Issue codes PayPal documents for a create/capture on a wallet that cannot pay. Anything else under 422 is still a
# business failure for that member; `decline_code()` reports whatever PayPal said.
DECLINE_CODES = frozenset(
    {
        "INSTRUMENT_DECLINED",
        "TRANSACTION_REFUSED",
        "PAYER_CANNOT_PAY",
        "PAYER_ACCOUNT_RESTRICTED",
        "PAYER_ACCOUNT_LOCKED_OR_CLOSED",
        "PAYEE_ACCOUNT_RESTRICTED",
        "PAYMENT_DENIED",
        "TOKEN_NOT_FOUND",
        "INVALID_RESOURCE_ID",
        "PAYER_ACTION_REQUIRED",
        "COMPLIANCE_VIOLATION",
    }
)


class CaptureSummary(BaseModel):
    """What the ledger needs from a COMPLETED order: ids and the net amount after PayPal's fee."""

    order_id: str
    capture_id: str | None = None
    status: str
    gross: Decimal | None = None
    fee: Decimal | None = None
    net: Decimal | None = None
    currency: str = "USD"

    @property
    def completed(self) -> bool:
        return self.status == "COMPLETED"


def mock_response_header(code: str = DEFAULT_MOCK_CODE) -> dict[str, str]:
    """The sandbox negative-testing header that forces `code` on the next call."""
    return {MOCK_RESPONSE_HEADER: json.dumps({"mock_application_codes": code}, separators=(",", ":"))}


def _purchase_unit(
    amount: Decimal, currency: str, reference_id: str | None, custom_id: str | None, description: str | None
) -> dict:
    unit: dict = {"amount": {"currency_code": currency, "value": money_str(amount)}}
    if reference_id:
        unit["reference_id"] = check_len(reference_id, 256, "reference_id")
    if custom_id:
        unit["custom_id"] = check_len(custom_id, 255, "custom_id")
    if description:
        unit["description"] = check_len(description, 127, "description")
    return unit


async def charge_vaulted(
    gw: Gateway,
    *,
    request_id: str,
    vault_id: str,
    amount: Decimal,
    currency: str = "USD",
    reference_id: str,
    custom_id: str,
    description: str,
    mock_decline: bool = False,
    mock_code: str = DEFAULT_MOCK_CODE,
) -> dict:
    """POST /v2/checkout/orders (intent CAPTURE, payment_source.paypal.vault_id): a merchant-initiated charge.

    For a vaulted PayPal wallet the order completes synchronously: the response is the order with status COMPLETED
    and `purchase_units[0].payments.captures[0]` (see `capture_summary`). A decline raises `PayPalError` (422,
    issue e.g. INSTRUMENT_DECLINED); no order or webhook exists in that case. `mock_decline=True` adds the
    sandbox `PayPal-Mock-Response` header so the decline is deterministic ("API decline: sandbox-injected").
    """
    body = {
        "intent": "CAPTURE",
        "purchase_units": [_purchase_unit(amount, currency, reference_id, custom_id, description)],
        "payment_source": {"paypal": {"vault_id": vault_id}},
    }
    headers = mock_response_header(mock_code) if mock_decline else None
    resp = await gw.call("POST", ORDERS_PATH, json=body, headers=headers, request_id=request_id)
    return resp.data


async def get_order(gw: Gateway, *, order_id: str) -> dict:
    resp = await gw.call("GET", f"{ORDERS_PATH}/{order_id}")
    return resp.data


def first_capture(order: dict) -> dict | None:
    units = order.get("purchase_units") or []
    if not units or not isinstance(units[0], dict):
        return None
    captures = (units[0].get("payments") or {}).get("captures") or []
    return captures[0] if captures and isinstance(captures[0], dict) else None


def capture_summary(order: dict) -> CaptureSummary:
    """Parse an order dict (create or GET response) into ids + gross/fee/net from seller_receivable_breakdown."""
    cap = first_capture(order) or {}
    breakdown = cap.get("seller_receivable_breakdown") or {}
    amount = cap.get("amount") or {}
    gross = to_decimal(breakdown.get("gross_amount")) or to_decimal(amount)
    currency = (
        (breakdown.get("gross_amount") or {}).get("currency_code") if isinstance(breakdown.get("gross_amount"), dict) else None
    ) or amount.get("currency_code") or "USD"
    return CaptureSummary(
        order_id=str(order.get("id") or ""),
        capture_id=str(cap["id"]) if cap.get("id") else None,
        status=str(cap.get("status") or order.get("status") or "UNKNOWN"),
        gross=gross,
        fee=to_decimal(breakdown.get("paypal_fee")),
        net=to_decimal(breakdown.get("net_amount")),
        currency=str(currency),
    )


async def create_vault_with_purchase_order(
    gw: Gateway,
    *,
    request_id: str,
    amount: Decimal,
    return_url: str,
    cancel_url: str,
    currency: str = "USD",
    reference_id: str | None = None,
    custom_id: str | None = None,
    description: str | None = None,
    usage_pattern: str = "RECURRING_PREPAID",
    brand_name: str = "Turnkeeper",
    customer_id: str | None = None,
) -> dict:
    """F18 fallback join: an approved-and-captured order that also vaults the wallet (`store_in_vault: ON_SUCCESS`).

    The member approves via `approve_link(order)`; then `capture_order` completes the payment, and the vault id
    appears at `payment_source.paypal.attributes.vault.id` on the capture response.
    """
    attributes: dict = {
        "vault": {
            "store_in_vault": "ON_SUCCESS",
            "usage_type": "MERCHANT",
            "usage_pattern": usage_pattern,
            "customer_type": "CONSUMER",
            "permit_multiple_payment_tokens": False,
        }
    }
    if customer_id:
        attributes["customer"] = {"id": customer_id}
    body = {
        "intent": "CAPTURE",
        "purchase_units": [_purchase_unit(amount, currency, reference_id, custom_id, description)],
        "payment_source": {
            "paypal": {
                "attributes": attributes,
                "experience_context": {
                    "brand_name": brand_name,
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                },
            }
        },
    }
    resp = await gw.call("POST", ORDERS_PATH, json=body, request_id=request_id)
    return resp.data


async def capture_order(gw: Gateway, *, order_id: str, request_id: str) -> dict:
    """POST /v2/checkout/orders/{id}/capture (empty body) after the payer approved."""
    resp = await gw.call("POST", f"{ORDERS_PATH}/{order_id}/capture", request_id=request_id)
    return resp.data


def approve_link(order: dict) -> str | None:
    return find_link(order, *APPROVE_RELS)


def decline_code(err: PayPalError) -> str | None:
    """The PayPal issue code behind a failed charge (e.g. "INSTRUMENT_DECLINED"), or the error name for a 4xx."""
    codes = err.issue_codes
    if codes:
        return codes[0]
    return err.name if 400 <= err.status < 500 else None


def is_decline(err: PayPalError) -> bool:
    """True when the error means this member cannot be charged right now (a business 422, never a 429/5xx)."""
    return err.status == 422 or bool(DECLINE_CODES & set(err.issue_codes))
