"""D1 smoke (c): `PayPal-Mock-Response` INSTRUMENT_DECLINED on a vault order (expect a 4xx and no order).

Usage:
    .venv/Scripts/python -I scripts/smoke/c_mock_decline.py --vault-id <vault_id> [--amount 1.00] [--code INSTRUMENT_DECLINED]
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.paypal import keys, orders
from app.paypal.client import PayPalError
from scripts.smoke._common import (
    SMOKE_CIRCLE_ID,
    SmokeFailure,
    SmokeGateway,
    emit,
    money,
    run,
    settings_or_exit,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vault-id", required=True)
    p.add_argument("--amount", type=money, default=Decimal("1.00"))
    p.add_argument("--code", default=orders.DEFAULT_MOCK_CODE, help="mock_application_codes value")
    p.add_argument("--member", default="ravi")
    return p.parse_args()


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    run_id = keys.new_run_id()
    request_id = keys.order_request_id(run_id, SMOKE_CIRCLE_ID, 1, args.member, 1)
    emit("plan", run_id=run_id, request_id=request_id, mock_header=orders.mock_response_header(args.code))

    async with SmokeGateway(settings) as sg:
        try:
            order = await orders.charge_vaulted(
                sg.gw,
                request_id=request_id,
                vault_id=args.vault_id,
                amount=args.amount,
                reference_id=f"{SMOKE_CIRCLE_ID}:1:{args.member}",
                custom_id=f"tk:{run_id}:{SMOKE_CIRCLE_ID}:1:{args.member}",
                description="Turnkeeper smoke mock decline",
                mock_decline=True,
                mock_code=args.code,
            )
        except PayPalError as e:
            emit(
                "decline",
                ok=400 <= e.status < 500,
                status=e.status,
                name=e.name,
                issues=e.issue_codes,
                decline_code=orders.decline_code(e),
                is_decline=orders.is_decline(e),
                debug_id=e.debug_id,
                message=e.message,
            )
            if not (400 <= e.status < 500):
                raise SmokeFailure(f"expected a 4xx business error, got {e.status}") from e
            if args.code not in e.issue_codes:
                raise SmokeFailure(f"expected issue {args.code}, got {e.issue_codes or e.name}") from e
            return True
        summary = orders.capture_summary(order)
        emit(
            "order", ok=False, order_id=summary.order_id, status=summary.status, capture_id=summary.capture_id
        )
        raise SmokeFailure("the mocked order was NOT declined (an order/capture exists; refund it)")


if __name__ == "__main__":
    run(main)
