"""D1 smoke (b): merchant-initiated CAPTURE on a vaulted PayPal wallet; records paypal_fee and net_amount.

Usage:
    .venv/Scripts/python -I scripts/smoke/b_mit_capture.py --vault-id <vault_id> --amount 1.00
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
    p.add_argument("--vault-id", required=True, help="payment token id from a_setup_token.py")
    p.add_argument("--amount", type=money, default=Decimal("1.00"))
    p.add_argument("--currency", default="USD")
    p.add_argument("--member", default="demo")
    p.add_argument("--round", type=int, default=1, dest="round_n")
    p.add_argument("--attempt", type=int, default=1)
    return p.parse_args()


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    run_id = keys.new_run_id()
    request_id = keys.order_request_id(run_id, SMOKE_CIRCLE_ID, args.round_n, args.member, args.attempt)
    emit("plan", run_id=run_id, request_id=request_id, amount=args.amount, currency=args.currency)

    async with SmokeGateway(settings) as sg:
        order = await orders.charge_vaulted(
            sg.gw,
            request_id=request_id,
            vault_id=args.vault_id,
            amount=args.amount,
            currency=args.currency,
            reference_id=f"{SMOKE_CIRCLE_ID}:{args.round_n}:{args.member}",
            custom_id=f"tk:{run_id}:{SMOKE_CIRCLE_ID}:{args.round_n}:{args.member}",
            description=f"Turnkeeper smoke round {args.round_n}",
        )
        summary = orders.capture_summary(order)
        emit(
            "order",
            ok=summary.completed,
            order_id=summary.order_id,
            order_status=order.get("status"),
            capture_id=summary.capture_id,
            capture_status=summary.status,
            gross=summary.gross,
            paypal_fee=summary.fee,
            net=summary.net,
            currency=summary.currency,
        )
        if not summary.completed or not summary.capture_id:
            raise SmokeFailure(f"order {summary.order_id} is {summary.status}, expected a COMPLETED capture")

        again = await orders.get_order(sg.gw, order_id=summary.order_id)
        confirm = orders.capture_summary(again)
        emit(
            "get_order",
            ok=confirm.completed,
            order_id=confirm.order_id,
            capture_id=confirm.capture_id,
            status=confirm.status,
        )
        if confirm.capture_id != summary.capture_id:
            raise SmokeFailure("GET order returned a different capture id")
    print(
        f"\norder_id={summary.order_id}  capture_id={summary.capture_id}  fee={summary.fee}  net={summary.net}\n",
        flush=True,
    )
    return True


if __name__ == "__main__":
    run(main)
