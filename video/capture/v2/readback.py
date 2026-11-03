"""Read a Payouts batch back from the real PayPal sandbox (GET /v1/payments/payouts/{batch_id}) and write the fields the
film shows. Read-only. Nothing secret is printed or stored (the token never leaves the gateway).

    python -I video/capture/v2/readback.py --batch <BATCH_ID> --out video/remotion/src/data/paypal_readback.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from app.paypal import payouts  # noqa: E402
from scripts.smoke._common import SmokeGateway, settings_or_exit  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    settings = settings_or_exit()
    async with SmokeGateway(settings) as sg:
        b = await payouts.get_payout_batch(sg.gw, batch_id=a.batch)
    h = b.get("batch_header", {})
    items = b.get("items", [])
    it = items[0] if items else {}

    def amt(d):
        return (d or {}).get("value")

    out = {
        "request": f"GET /v1/payments/payouts/{a.batch}",
        "host": "api-m.sandbox.paypal.com",
        "payout_batch_id": h.get("payout_batch_id"),
        "batch_status": h.get("batch_status"),
        "amount": amt(h.get("amount")),
        "fees": amt(h.get("fees")),
        "currency": (h.get("amount") or {}).get("currency"),
        "time_completed": h.get("time_completed"),
        "item_count": len(items),
        "payout_item_id": it.get("payout_item_id"),
        "transaction_status": it.get("transaction_status"),
        "item_amount": amt((it.get("payout_item") or {}).get("amount")),
        "item_fee": amt(it.get("payout_item_fee")),
        "sender_item_id": (it.get("payout_item") or {}).get("sender_item_id"),
    }
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out))


asyncio.run(main())
