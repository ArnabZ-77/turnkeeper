"""Transaction Search v1 - Balances: the account-wide available balance checked before every payout.

Confirmed against the Transaction Search v1.10 OpenAPI schema (2026-10-22): `GET /v1/reporting/balances`
(query `currency_code`, optional `as_of_time`) -> `{"balances": [{"currency", "primary", "total_balance",
"available_balance": {"currency_code", "value"}, "withheld_balance"}], "account_id", "as_of_time",
"last_refresh_time"}`. Needs the Transaction Search / Reporting feature on the sandbox app (403 otherwise).
The figure is account-wide; the per-circle ledger is the binding check (PLAN.md section 3 step 10).
"""

from __future__ import annotations

from datetime import UTC, datetime
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


# --- D1 finding (2026-10-23): Balances lags ~2.5 h in the sandbox ------------------------------------
# A capture made minutes ago is not in `available_balance` yet, so the figure is context, never the binding
# payout check (the per-circle ledger is). A reading whose last_refresh_time is older than STALE_AFTER_S is
# "stale" and must never block a payout.

BALANCES_SOURCE = "paypal_balances"
STALE_AFTER_S = 15 * 60


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def balance_context(
    balances: dict, currency: str = "USD", *, now: datetime | None = None, stale_after_s: int = STALE_AFTER_S
) -> dict:
    """`{available, last_refresh_time, source: "paypal_balances", stale, age_s}` for display and policy context.

    `stale` is True when last_refresh_time is known and older than `stale_after_s` (wall clock); unknown
    freshness is reported as `stale: None`."""
    amount = available_amount(balances, currency)
    refreshed_raw = last_refresh_time(balances)
    refreshed = _parse_time(refreshed_raw)
    age_s: int | None = None
    stale: bool | None = None
    if refreshed is not None:
        age_s = max(0, int(((now or datetime.now(UTC)) - refreshed).total_seconds()))
        stale = age_s > stale_after_s
    return {
        "available": str(amount) if amount is not None else None,
        "last_refresh_time": refreshed_raw,
        "source": BALANCES_SOURCE,
        "stale": stale,
        "age_s": age_s,
    }


def _age_text(age_s: int) -> str:
    if age_s < 120:
        return f"{age_s} s"
    if age_s < 2 * 3600:
        return f"{age_s // 60} min"
    return f"{age_s / 3600:.1f} h"


def balance_label(context: dict | None, currency: str = "USD") -> str | None:
    """One line for summaries: "7885.93 USD (stale: last refreshed 2026-10-24 02:29 UTC, 1.5 h old)".

    Live run 2026-10-25 (R3): a stale reading used to read as "PayPal available n/a" although Balances answered.
    The figure is always shown with its refresh time; None when Balances gave no figure at all."""
    if not context or context.get("available") in (None, ""):
        return None
    refreshed = _parse_time(context.get("last_refresh_time"))
    when = refreshed.strftime("%Y-%m-%d %H:%M UTC") if refreshed is not None else None
    age_s = context.get("age_s")
    age = f", {_age_text(int(age_s))} old" if isinstance(age_s, int | float) else ""
    amount = f"{context['available']} {currency}"
    if context.get("stale") is True:
        return f"{amount} (stale: last refreshed {when}{age})" if when else f"{amount} (stale)"
    if when:
        return f"{amount} (last refreshed {when}{age})"
    return f"{amount} (refresh time unknown)"
