"""D1 smoke (g): PayPal Agent Toolkit `get_order_details` through our adapter (cached token, request id, gateway).

Usage:
    .venv/Scripts/python -I scripts/smoke/g_toolkit_wrap.py --order-id <order id from b_mit_capture.py>

PASS when the toolkit returns the same order (and capture) as the raw REST call and the whole script fetched
exactly ONE OAuth token (the toolkit's own client would fetch a new one per call).
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.paypal import keys, orders
from app.paypal.toolkit_adapter import ToolkitAdapter
from scripts.smoke._common import SMOKE_CIRCLE_ID, SmokeFailure, SmokeGateway, emit, run, settings_or_exit


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--order-id", required=True)
    return p.parse_args()


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    async with SmokeGateway(settings) as sg:
        adapter = ToolkitAdapter(sg.gw, sg.client)
        emit("adapter", tools=adapter.tool_names(), sandbox=adapter.context.sandbox)
        await adapter.prepare()
        fetches_after_prepare = sg.client.token_fetches

        request_id = keys.order_request_id(keys.new_run_id(), SMOKE_CIRCLE_ID, 1, "toolkit", 1)
        result = await adapter.run("get_order_details", {"order_id": args.order_id}, request_id=request_id)
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else result
        via_toolkit = orders.capture_summary(raw)
        emit(
            "toolkit_order",
            ok=via_toolkit.order_id == args.order_id,
            order_id=via_toolkit.order_id,
            status=via_toolkit.status,
            capture_id=via_toolkit.capture_id,
            gross=via_toolkit.gross,
            net=via_toolkit.net,
            result_keys=sorted(result.keys()),
        )
        if via_toolkit.order_id != args.order_id:
            raise SmokeFailure(f"toolkit returned order {via_toolkit.order_id!r}, expected {args.order_id!r}")

        via_rest = orders.capture_summary(await orders.get_order(sg.gw, order_id=args.order_id))
        emit(
            "rest_order",
            ok=via_rest.capture_id == via_toolkit.capture_id,
            capture_id=via_rest.capture_id,
            status=via_rest.status,
        )
        if via_rest.capture_id != via_toolkit.capture_id:
            raise SmokeFailure("toolkit and REST disagree on the capture id")

        emit(
            "token_injection",
            ok=sg.client.token_fetches == 1,
            token_fetches=sg.client.token_fetches,
            fetches_after_prepare=fetches_after_prepare,
            gateway_calls=int(sg.gw.stats["calls"]),
        )
        if sg.client.token_fetches != 1:
            raise SmokeFailure(f"expected exactly one OAuth token fetch, saw {sg.client.token_fetches}")
    return True


if __name__ == "__main__":
    run(main)
