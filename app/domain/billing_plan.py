"""Vault v3 ``billing_plan`` for the setup token, derived from the charter (PLAN.md section 4, call 1).

The plan is what the member sees on PayPal's consent screen: one REGULAR cycle per round at the charter amount,
monthly or weekly, starting on the charter's start date.

Sandbox-verified shape (D1, 2026-10-23): with ``usage_pattern=RECURRING_PREPAID`` PayPal requires
``one_time_charges`` and rejects ``pricing_model=FIXED`` (INCOMPATIBLE_PARAMETER_VALUE); ``VARIABLE`` with a
0.00 one-time charge is accepted. The charge amount itself is still enforced by our policy engine.
"""

from __future__ import annotations

from app.domain.charter import Charter, rounds_total
from app.domain.fees import wire_amount

_INTERVAL_UNIT = {"monthly": "MONTH", "weekly": "WEEK"}


def billing_plan_from_charter(charter: Charter) -> dict:
    """The ``payment_source.paypal.billing_plan`` object for ``POST /v3/vault/setup-tokens``."""
    return {
        "name": charter.name,
        "billing_cycles": [
            {
                "tenure_type": "REGULAR",
                "sequence": 1,
                "total_cycles": rounds_total(charter),
                "pricing_scheme": {
                    "pricing_model": "VARIABLE",
                    "price": {"value": wire_amount(charter.amount), "currency_code": charter.currency},
                },
                "frequency": {"interval_unit": _INTERVAL_UNIT[charter.cadence], "interval_count": 1},
                "start_date": charter.start_date.isoformat(),
            }
        ],
        "one_time_charges": {"total_amount": {"value": "0.00", "currency_code": charter.currency}},
    }
