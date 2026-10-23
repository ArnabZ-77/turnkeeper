"""D1 smoke (e): `GET /v1/reporting/balances` (Transaction Search) freshness right after a capture.

Usage:
    .venv/Scripts/python -I scripts/smoke/e_balances.py [--currency USD]

A 403 means the sandbox app lacks the "Transaction Search" feature; the engine then falls back to the ledger.
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.paypal import balances
from app.paypal.client import PayPalError
from scripts.smoke._common import SmokeFailure, SmokeGateway, emit, run, settings_or_exit


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--currency", default="USD")
    return p.parse_args()


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    async with SmokeGateway(settings) as sg:
        try:
            data = await balances.get_balances(sg.gw, currency=args.currency)
        except PayPalError as e:
            if e.status == 403:
                emit(
                    "balances",
                    ok=False,
                    status=403,
                    name=e.name,
                    note="enable Transaction Search on the sandbox app; ledger fallback",
                )
                raise SmokeFailure("balances endpoint is not permitted for this app (403)") from e
            raise
        available = balances.available_amount(data, args.currency)
        emit(
            "balances",
            ok=available is not None,
            currency=args.currency,
            available=available,
            total=balances.total_amount(data, args.currency),
            last_refresh_time=balances.last_refresh_time(data),
            account_id=data.get("account_id"),
            currencies=[b.get("currency") for b in data.get("balances") or [] if isinstance(b, dict)],
        )
        if available is None:
            raise SmokeFailure(f"no {args.currency} balance in the response")
    return True


if __name__ == "__main__":
    run(main)
