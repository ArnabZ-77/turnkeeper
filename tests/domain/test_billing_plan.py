from __future__ import annotations

import json

from app.domain.billing_plan import billing_plan_from_charter


def test_monthly_demo_plan_matches_plan_md_shape(charter):
    assert billing_plan_from_charter(charter) == {
        "name": "Temple Circle A",
        "billing_cycles": [
            {
                "tenure_type": "REGULAR",
                "sequence": 1,
                "total_cycles": 6,
                "pricing_scheme": {
                    "pricing_model": "VARIABLE",
                    "price": {"value": "200.00", "currency_code": "USD"},
                },
                "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                "start_date": "2026-11-01",
            }
        ],
        # Required by the sandbox for RECURRING_PREPAID (verified 2026-10-23); FIXED pricing is rejected.
        "one_time_charges": {"total_amount": {"value": "0.00", "currency_code": "USD"}},
    }


def test_weekly_plan_uses_week(make_charter):
    plan = billing_plan_from_charter(make_charter(cadence="weekly"))
    assert plan["billing_cycles"][0]["frequency"] == {"interval_unit": "WEEK", "interval_count": 1}


def test_price_is_two_place_string_and_cycles_match_members(make_charter):
    members = [{"handle": "a1", "name": "A"}, {"handle": "b1", "name": "B"}, {"handle": "c1", "name": "C"}]
    c = make_charter(amount="150", members=members, rotation=["a1", "b1", "c1"], quorum="2/3")
    cycle = billing_plan_from_charter(c)["billing_cycles"][0]
    assert cycle["total_cycles"] == 3
    assert cycle["pricing_scheme"]["price"]["value"] == "150.00"


def test_plan_is_plain_json(charter):
    plan = billing_plan_from_charter(charter)
    assert json.loads(json.dumps(plan)) == plan  # no Decimal or date objects inside
