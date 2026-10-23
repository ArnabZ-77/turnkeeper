"""Register (or look up / delete) the Turnkeeper webhook listener on the PayPal sandbox.

Usage:
    .venv/Scripts/python -I scripts/register_webhook.py [--url https://host/webhooks/paypal]   # default: PUBLIC_BASE_URL + /webhooks/paypal
    .venv/Scripts/python -I scripts/register_webhook.py --list
    .venv/Scripts/python -I scripts/register_webhook.py --delete <webhook_id>

Prints the webhook id; put it in `.env` as PAYPAL_WEBHOOK_ID yourself (this script never edits `.env`).
Output: one JSON line per step and a final PASS/FAIL (exit 0/1; 2 when credentials are missing).
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.paypal import webhooks
from app.paypal.client import PayPalError
from scripts.smoke._common import SmokeFailure, SmokeGateway, emit, run, settings_or_exit

WEBHOOK_PATH = "/webhooks/paypal"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=None, help="listener URL (default: PUBLIC_BASE_URL + /webhooks/paypal)")
    p.add_argument(
        "--events",
        nargs="*",
        default=None,
        help="event type names (default: app.paypal.webhooks.WEBHOOK_EVENTS)",
    )
    p.add_argument("--list", action="store_true", help="list the app's webhooks and exit")
    p.add_argument("--delete", default=None, metavar="WEBHOOK_ID", help="delete one webhook and exit")
    return p.parse_args()


async def main() -> bool:
    args = parse_args()
    settings = settings_or_exit()
    async with SmokeGateway(settings) as sg:
        if args.delete:
            await webhooks.delete_webhook(sg.gw, webhook_id=args.delete)
            emit("deleted", ok=True, webhook_id=args.delete)
            return True
        if args.list:
            listing = await webhooks.list_webhooks(sg.gw)
            hooks = [
                {"id": h.get("id"), "url": h.get("url"), "event_types": len(h.get("event_types") or [])}
                for h in listing.get("webhooks") or []
                if isinstance(h, dict)
            ]
            emit("webhooks", ok=True, count=len(hooks), webhooks=hooks)
            return True

        url = args.url or settings.public_base_url.rstrip("/") + WEBHOOK_PATH
        if not url.startswith("https://"):
            raise SmokeFailure(
                f"PayPal only delivers to https URLs, got {url!r} (set PUBLIC_BASE_URL or --url)"
            )
        events = args.events or webhooks.WEBHOOK_EVENTS
        try:
            hook = await webhooks.register_webhook(sg.gw, url=url, event_types=events)
            emit(
                "registered",
                ok=True,
                webhook_id=hook.get("id"),
                url=url,
                event_types=len(hook.get("event_types") or []),
            )
        except PayPalError as e:
            if e.status != 400 or "ALREADY_EXISTS" not in (e.name + " ".join(e.issue_codes)):
                raise
            listing = await webhooks.list_webhooks(sg.gw)
            hook = webhooks.find_webhook_by_url(listing, url) or {}
            if not hook.get("id"):
                raise SmokeFailure(
                    "PayPal says the URL is already registered but it is not in the listing"
                ) from e
            emit("already_registered", ok=True, webhook_id=hook.get("id"), url=url)
        webhook_id = str(hook.get("id") or "")
        if not webhook_id:
            raise SmokeFailure("no webhook id in the response")
        print(f"\nPAYPAL_WEBHOOK_ID={webhook_id}   <- add this to .env, then restart the app\n", flush=True)
        if settings.paypal_webhook_id and settings.paypal_webhook_id != webhook_id:
            emit("env", ok=False, note="PAYPAL_WEBHOOK_ID in .env is a different id; update it")
    return True


if __name__ == "__main__":
    run(main)
