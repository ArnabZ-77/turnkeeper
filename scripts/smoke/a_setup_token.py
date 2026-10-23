"""D1 smoke (a): Vault v3 setup token (RECURRING_PREPAID + billing_plan) -> member approval -> payment token.

Usage (from anywhere; `python -I` safe):
    .venv/Scripts/python -I scripts/smoke/a_setup_token.py [--amount 200.00] [--member demo] [--no-billing-plan]

Prints the approve link, waits for Enter while you approve in a sandbox PERSONAL account, then creates the
payment token and prints the `vault_id` (reuse it for b_mit_capture.py, c_mock_decline.py, g_toolkit_wrap.py).
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.paypal import keys, vault
from scripts.smoke._common import (
    SMOKE_CIRCLE_ID,
    SmokeFailure,
    SmokeGateway,
    emit,
    money,
    run,
    settings_or_exit,
    wait_for_enter,
)

FIXTURE = REPO_ROOT / "app" / "fixtures" / "demo_circle.json"


def build_billing_plan(amount: Decimal) -> dict:
    """The demo charter's billing plan (app.domain.billing_plan) with the amount overridden; inline fallback."""
    try:
        from app.domain.billing_plan import billing_plan_from_charter
        from app.domain.charter import Charter

        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        charter_data = dict(raw.get("charter", raw))
        charter_data["amount"] = str(amount)
        return billing_plan_from_charter(Charter.model_validate(charter_data))
    except Exception as e:  # noqa: BLE001 - the smoke test must still run when the fixture is being edited
        emit("billing_plan", ok=True, note=f"fixture unavailable ({type(e).__name__}); using the inline plan")
        return {
            "name": "Turnkeeper smoke circle",
            "billing_cycles": [
                {
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 6,
                    "pricing_scheme": {
                        "pricing_model": "FIXED",
                        "price": {"value": f"{amount:.2f}", "currency_code": "USD"},
                    },
                    "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                    "start_date": "2026-11-01",
                }
            ],
        }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--amount", type=money, default=Decimal("200.00"), help="charter amount shown on the consent page"
    )
    p.add_argument("--member", default="demo", help="member handle used in the PayPal-Request-Id")
    p.add_argument("--no-billing-plan", action="store_true", help="omit billing_plan (Line B fallback)")
    p.add_argument("--usage-pattern", default="RECURRING_PREPAID")
    p.add_argument("--customer-id", default=None, help="reuse an existing PayPal customer id")
    p.add_argument("--list", action="store_true", help="after vaulting, list the customer's payment tokens")
    return p.parse_args()


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    run_id = keys.new_run_id()
    request_id = keys.setup_request_id(run_id, SMOKE_CIRCLE_ID, args.member)
    plan = None if args.no_billing_plan else build_billing_plan(args.amount)
    emit("plan", run_id=run_id, request_id=request_id, billing_plan=plan, usage_pattern=args.usage_pattern)

    async with SmokeGateway(settings) as sg:
        setup = await vault.create_setup_token(
            sg.gw,
            request_id=request_id,
            description=f"Turnkeeper smoke circle {run_id}",
            billing_plan=plan,
            return_url=f"{settings.public_base_url}/join/return",
            cancel_url=f"{settings.public_base_url}/join/cancel",
            usage_pattern=args.usage_pattern,
            customer_id=args.customer_id,
        )
        setup_token_id = str(setup.get("id") or "")
        link = vault.approve_link(setup)
        emit(
            "setup_token",
            ok=bool(setup_token_id),
            setup_token_id=setup_token_id,
            status=setup.get("status"),
            approve_link=link,
        )
        if not setup_token_id or not link:
            raise SmokeFailure("setup token response has no id or approve link")

        print(f"\nApprove in a sandbox PERSONAL account:\n  {link}\n", flush=True)
        wait_for_enter("Press Enter after approving (the return page may 404; that is fine)...")

        status = await vault.get_setup_token(sg.gw, setup_token_id=setup_token_id)
        emit("setup_token_status", setup_token_id=setup_token_id, status=status.get("status"))
        if status.get("status") not in {"APPROVED", "VAULTED", "TOKENIZED"}:
            raise SmokeFailure(f"setup token is {status.get('status')}, not APPROVED")

        token = await vault.create_payment_token(
            sg.gw, setup_token_id=setup_token_id, request_id=f"{request_id}:pt"
        )
        vault_id = vault.vault_id_of(token)
        customer_id = vault.customer_id_of(token)
        source = (token.get("payment_source") or {}).get("paypal") or {}
        emit(
            "payment_token",
            ok=True,
            vault_id=vault_id,
            customer_id=customer_id,
            usage_pattern=source.get("usage_pattern"),
            usage_type=source.get("usage_type"),
            has_billing_plan=bool(source.get("billing_plan")),
        )
        if args.list and customer_id:
            listing = await vault.list_payment_tokens(sg.gw, customer_id=customer_id)
            emit("payment_tokens", customer_id=customer_id, count=len(listing.get("payment_tokens") or []))
    print(f"\nvault_id={vault_id}  customer_id={customer_id}\n", flush=True)
    return True


if __name__ == "__main__":
    run(main)
