"""Transaction Search v1 - Balances: the account-wide available balance checked before every payout.

Confirmed against the Transaction Search v1.10 OpenAPI schema (2026-10-22): `GET /v1/reporting/balances`
(query `currency_code`, optional `as_of_time`) -> `{"balances": [{"currency", "primary", "total_balance",
"available_balance": {"currency_code", "value"}, "withheld_balance"}], "account_id", "as_of_time",
"last_refresh_time"}`. Needs the Transaction Search / Reporting feature on the sandbox app (403 otherwise).
The figure is account-wide; the per-circle ledger is the binding check (PLAN.md section 3 step 10).
"""

from __future__ import annotations

from decimal import Decimal

from app.paypal._util import to_decimal
from app.paypal.gateway import Gateway

BALANCES_PATH = "/v1/reporting/balances"


async def get_balances(gw: Gateway, *, currency: str = "USD", as_of_time: str | None = None) -> dict:
    params: dict = {"currency_code": currency}
    if as_of_time:
        params["as_of_time"] = as_of_time
    resp = await gw.call("GET", BALANCES_PATH, params=params)
    return resp.data


def _balance_for(balances: dict, currency: str) -> dict | None:
    for entry in balances.get("balances") or []:
        if isinstance(entry, dict) and entry.get("currency") == currency:
            return entry
    return None


def available_amount(balances: dict, currency: str = "USD") -> Decimal | None:
    """`available_balance.value` for `currency`, or None when the response has no such balance."""
    entry = _balance_for(balances, currency)
    if entry is None:
        return None
    return to_decimal(entry.get("available_balance"))


def total_amount(balances: dict, currency: str = "USD") -> Decimal | None:
    entry = _balance_for(balances, currency)
    if entry is None:
        return None
    return to_decimal(entry.get("total_balance"))


def last_refresh_time(balances: dict) -> str | None:
    value = balances.get("last_refresh_time") or balances.get("as_of_time")
    return str(value) if value else None
