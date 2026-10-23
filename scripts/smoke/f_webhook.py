"""D1 smoke (f): register the webhook on the public URL, then watch the running app receive a real delivery.

Usage:
    .venv/Scripts/python -I scripts/smoke/f_webhook.py --public-url https://<tunnel-or-render-host>
        [--vault-id <vault_id>] [--timeout 60] [--allow-unverified]

Steps: POST /v1/notifications/webhooks (or reuse the one already registered for that URL), print the webhook
id, optionally trigger a $1.00 capture (`--vault-id`) so PayPal sends PAYMENT.CAPTURE.COMPLETED, then poll
`<public-url>/api/debug/webhooks` for up to 60 s. PASS when a new event arrives with `verified: true`
(the app must have PAYPAL_WEBHOOK_ID set to the printed id and be restarted; `--allow-unverified` relaxes that).
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.paypal import keys, orders, webhooks
from app.paypal.client import PayPalError
from app.paypal.gateway import Gateway
from scripts.smoke._common import SMOKE_CIRCLE_ID, SmokeFailure, SmokeGateway, emit, run, settings_or_exit

WEBHOOK_PATH = "/webhooks/paypal"
DEBUG_PATH = "/api/debug/webhooks"
POLL_INTERVAL_S = 5.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--public-url", required=True, help="https base URL where this app is reachable by PayPal")
    p.add_argument(
        "--vault-id",
        default=None,
        help="trigger a $1.00 capture to produce a PAYMENT.CAPTURE.COMPLETED event",
    )
    p.add_argument("--timeout", type=float, default=60.0, help="seconds to poll /api/debug/webhooks")
    p.add_argument(
        "--allow-unverified", action="store_true", help="PASS even when the event is stored unverified"
    )
    return p.parse_args()


async def ensure_webhook(gw: Gateway, url: str) -> dict:
    """Register the listener; when PayPal says the URL is already registered, reuse that webhook."""
    try:
        return await webhooks.register_webhook(gw, url=url, event_types=webhooks.WEBHOOK_EVENTS)
    except PayPalError as e:
        if e.status != 400 or not ("ALREADY_EXISTS" in e.name or "ALREADY_EXISTS" in " ".join(e.issue_codes)):
            raise
        listing = await webhooks.list_webhooks(gw)
        existing = webhooks.find_webhook_by_url(listing, url)
        if existing is None:
            raise SmokeFailure("PayPal reports the URL is registered but it is not in the listing") from e
        emit("webhook_reused", webhook_id=existing.get("id"), url=url)
        return existing


async def fetch_debug(http: httpx.AsyncClient, url: str) -> list[dict]:
    resp = await http.get(url)
    resp.raise_for_status()
    data = resp.json()
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    base = args.public_url.rstrip("/")
    listener_url = base + WEBHOOK_PATH
    debug_url = base + DEBUG_PATH
    emit("plan", listener_url=listener_url, debug_url=debug_url, events=len(webhooks.WEBHOOK_EVENTS))

    async with SmokeGateway(settings) as sg, httpx.AsyncClient(timeout=15.0) as http:
        hook = await ensure_webhook(sg.gw, listener_url)
        webhook_id = str(hook.get("id") or "")
        emit(
            "webhook",
            ok=bool(webhook_id),
            webhook_id=webhook_id,
            event_types=len(hook.get("event_types") or []),
        )
        if not webhook_id:
            raise SmokeFailure("webhook registration returned no id")
        print(
            f"\nwebhook_id={webhook_id}  (PAYPAL_WEBHOOK_ID in .env must equal this; restart the app after changing it)\n",
            flush=True,
        )
        if settings.paypal_webhook_id and settings.paypal_webhook_id != webhook_id:
            emit(
                "webhook",
                ok=False,
                note="PAYPAL_WEBHOOK_ID in .env differs from the registered id; verification will fail",
            )

        try:
            before = await fetch_debug(http, debug_url)
        except (httpx.HTTPError, ValueError) as e:
            raise SmokeFailure(
                f"cannot read {debug_url}: {type(e).__name__}: {e} (is the app running behind that URL?)"
            ) from e
        seen = {row.get("event_id") for row in before}
        emit("baseline", stored_events=len(before))

        if args.vault_id:
            run_id = keys.new_run_id()
            order = await orders.charge_vaulted(
                sg.gw,
                request_id=keys.order_request_id(run_id, SMOKE_CIRCLE_ID, 1, "hook", 1),
                vault_id=args.vault_id,
                amount=Decimal("1.00"),
                reference_id=f"{SMOKE_CIRCLE_ID}:1:hook",
                custom_id=f"tk:{run_id}:{SMOKE_CIRCLE_ID}:1:hook",
                description="Turnkeeper smoke webhook trigger",
            )
            summary = orders.capture_summary(order)
            emit("trigger", ok=summary.completed, order_id=summary.order_id, capture_id=summary.capture_id)
        else:
            print(
                "No --vault-id: trigger an event yourself now (e.g. run b_mit_capture.py in another shell).",
                flush=True,
            )

        deadline = time.monotonic() + args.timeout
        new_rows: list[dict] = []
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL_S)
            rows = await fetch_debug(http, debug_url)
            new_rows = [r for r in rows if r.get("event_id") not in seen]
            emit(
                "poll",
                stored_events=len(rows),
                new_events=len(new_rows),
                seconds_left=int(deadline - time.monotonic()),
            )
            if new_rows:
                break
        if not new_rows:
            raise SmokeFailure(f"no webhook delivery reached {debug_url} within {args.timeout:.0f} s")
        verified = any(bool(r.get("verified")) for r in new_rows)
        emit(
            "delivery",
            ok=verified or args.allow_unverified,
            events=[
                {k: r.get(k) for k in ("event_id", "event_type", "resource_id", "verified", "received_at")}
                for r in new_rows
            ],
            verified=verified,
        )
        if not verified and not args.allow_unverified:
            raise SmokeFailure(
                "event stored but not verified: set PAYPAL_WEBHOOK_ID to the id above and restart the app"
            )
    return True


if __name__ == "__main__":
    run(main)
