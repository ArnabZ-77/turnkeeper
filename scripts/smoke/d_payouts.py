"""D1 smoke (d): Payouts v1 -> PAYPAL email (fee recorded); optional VENMO phone; optional UNCLAIMED -> cancel;
then the same `sender_batch_id` is re-posted to prove PayPal dedupes it.

Usage:
    .venv/Scripts/python -I scripts/smoke/d_payouts.py --receiver <sandbox-personal-email> [--amount 1.00]
        [--venmo +15551234567] [--unclaimed] [--unclaimed-receiver nobody@example.com] [--poll 60]
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.paypal import keys, payouts
from app.paypal.client import PayPalError
from app.paypal.gateway import Gateway
from scripts.smoke._common import (
    SMOKE_CIRCLE_ID,
    SmokeFailure,
    SmokeGateway,
    emit,
    money,
    run,
    settings_or_exit,
)

POLL_INTERVAL_S = 5.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--receiver", required=True, help="email of a sandbox PERSONAL account (PAYPAL wallet)")
    p.add_argument("--amount", type=money, default=Decimal("1.00"))
    p.add_argument(
        "--venmo", default=None, metavar="PHONE", help="E.164 phone of a US sandbox personal (VENMO wallet)"
    )
    p.add_argument(
        "--unclaimed", action="store_true", help="also pay an address with no account, then cancel it"
    )
    p.add_argument(
        "--unclaimed-receiver", default=None, help="email with no sandbox account (default: generated)"
    )
    p.add_argument("--poll", type=float, default=60.0, help="seconds to wait for a final item status")
    p.add_argument("--skip-dedupe", action="store_true")
    return p.parse_args()


async def poll_item(
    gw: Gateway, batch_id: str, *, until: set[str], timeout_s: float
) -> tuple[dict, dict | None]:
    """GET the batch until its first item reaches one of `until` (or a final status), or timeout."""
    deadline = time.monotonic() + timeout_s
    while True:
        batch = await payouts.get_payout_batch(gw, batch_id=batch_id)
        item = payouts.first_item(batch)
        status = payouts.item_status(item)
        emit("poll", batch_id=batch_id, batch_status=payouts.batch_status(batch), item_status=status)
        if status in until or status in payouts.FINAL_ITEM_STATUSES:
            return batch, item
        if time.monotonic() >= deadline:
            return batch, item
        await asyncio.sleep(POLL_INTERVAL_S)


async def send(
    gw: Gateway, *, run_id: str, round_n: int, item: payouts.PayoutItem, subject: str
) -> tuple[str, dict]:
    batch_id_key = keys.payout_batch_id(run_id, SMOKE_CIRCLE_ID, round_n, 1)
    created = await payouts.create_payout(
        gw, sender_batch_id=batch_id_key, items=[item], email_subject=subject
    )
    batch_id = payouts.batch_id_of(created)
    emit(
        "payout_created",
        ok=bool(batch_id),
        sender_batch_id=batch_id_key,
        batch_id=batch_id,
        batch_status=payouts.batch_status(created),
        wallet=item.wallet,
        amount=item.amount,
    )
    if not batch_id:
        raise SmokeFailure("payout response has no payout_batch_id")
    return batch_id_key, created


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    run_id = keys.new_run_id()
    emit("plan", run_id=run_id, receiver=args.receiver, venmo=bool(args.venmo), unclaimed=args.unclaimed)
    ok = True

    async with SmokeGateway(settings) as sg:
        # 1) PAYPAL wallet by email.
        item = payouts.PayoutItem(
            recipient_type="EMAIL",
            receiver=args.receiver,
            wallet="PAYPAL",
            amount=args.amount,
            note="Turnkeeper smoke payout",
            sender_item_id=keys.payout_item_id(run_id, SMOKE_CIRCLE_ID, 1, "demo"),
        )
        sender_batch_id, created = await send(
            sg.gw, run_id=run_id, round_n=1, item=item, subject="Turnkeeper smoke payout"
        )
        batch_id = payouts.batch_id_of(created) or ""
        batch, first = await poll_item(sg.gw, batch_id, until={"SUCCESS", "UNCLAIMED"}, timeout_s=args.poll)
        status = payouts.item_status(first)
        emit(
            "paypal_item",
            ok=status in {"SUCCESS", "PENDING"},
            batch_id=batch_id,
            item_id=payouts.item_id_of(first),
            status=status,
            payout_item_fee=payouts.item_fee(first),
            batch_fees=payouts.batch_fees(batch),
        )
        if status not in {"SUCCESS", "PENDING"}:
            ok = False

        # 2) Dedupe: the same sender_batch_id must not create a second batch.
        if not args.skip_dedupe:
            try:
                dup = await payouts.create_payout(
                    sg.gw,
                    sender_batch_id=sender_batch_id,
                    items=[item],
                    email_subject="Turnkeeper smoke payout",
                )
                same = payouts.batch_id_of(dup) == batch_id
                emit(
                    "dedupe",
                    ok=same,
                    outcome="same_batch_returned" if same else "NEW_BATCH_CREATED",
                    batch_id=payouts.batch_id_of(dup),
                )
                ok = ok and same
            except PayPalError as e:
                rejected = 400 <= e.status < 500
                emit(
                    "dedupe",
                    ok=rejected,
                    outcome="rejected",
                    status=e.status,
                    name=e.name,
                    issues=e.issue_codes,
                )
                ok = ok and rejected

        # 3) VENMO wallet by phone.
        if args.venmo:
            venmo_item = payouts.PayoutItem(
                recipient_type="PHONE",
                receiver=args.venmo,
                wallet="VENMO",
                amount=args.amount,
                note="Turnkeeper smoke payout (Venmo)",
                sender_item_id=keys.payout_item_id(run_id, SMOKE_CIRCLE_ID, 2, "venmo"),
            )
            try:
                _, created_v = await send(
                    sg.gw, run_id=run_id, round_n=2, item=venmo_item, subject="Turnkeeper smoke Venmo"
                )
                batch_v, item_v = await poll_item(
                    sg.gw,
                    payouts.batch_id_of(created_v) or "",
                    until={"SUCCESS", "UNCLAIMED"},
                    timeout_s=args.poll,
                )
                status_v = payouts.item_status(item_v)
                emit(
                    "venmo_item",
                    ok=status_v in {"SUCCESS", "PENDING"},
                    status=status_v,
                    payout_item_fee=payouts.item_fee(item_v),
                )
            except PayPalError as e:
                emit(
                    "venmo_item",
                    ok=False,
                    status=e.status,
                    name=e.name,
                    issues=e.issue_codes,
                    note="keep/cut decision",
                )

        # 4) UNCLAIMED -> cancel (funds return to the sender).
        if args.unclaimed:
            receiver = args.unclaimed_receiver or f"tk-noaccount-{run_id}@example.com"
            unclaimed_item = payouts.PayoutItem(
                recipient_type="EMAIL",
                receiver=receiver,
                wallet="PAYPAL",
                amount=args.amount,
                note="Turnkeeper smoke unclaimed",
                sender_item_id=keys.payout_item_id(run_id, SMOKE_CIRCLE_ID, 3, "ghost"),
            )
            _, created_u = await send(
                sg.gw, run_id=run_id, round_n=3, item=unclaimed_item, subject="Turnkeeper smoke unclaimed"
            )
            _, item_u = await poll_item(
                sg.gw, payouts.batch_id_of(created_u) or "", until={"UNCLAIMED"}, timeout_s=args.poll
            )
            status_u = payouts.item_status(item_u)
            item_id_u = payouts.item_id_of(item_u)
            emit("unclaimed_item", ok=status_u == "UNCLAIMED", item_id=item_id_u, status=status_u)
            if status_u == "UNCLAIMED" and item_id_u:
                cancelled = await payouts.cancel_payout_item(sg.gw, item_id=item_id_u)
                c_status = payouts.item_status(cancelled)
                emit("cancel", ok=c_status == "RETURNED", item_id=item_id_u, status=c_status)
                ok = ok and c_status == "RETURNED"
            else:
                ok = False
    return ok


if __name__ == "__main__":
    run(main)
