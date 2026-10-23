"""Shared plumbing for the D1 sandbox smoke scripts (docs/d1-smoke.md).

Every script: argparse -> asyncio.run(main) -> one JSON object per line on stdout -> a final `PASS` or `FAIL`
line. Exit codes: 0 PASS, 1 FAIL (PayPal said no, or an assertion did not hold), 2 configuration/usage error
(no sandbox credentials, bad arguments), 130 interrupted. Tokens, secrets and `Authorization` headers are
never printed: `redact()` strips token-like keys from anything that is echoed.

The scripts are `python -I` safe: each inserts the repo root into `sys.path` before importing this module,
and this module changes the working directory to the repo root so `app.config` finds `.env`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)  # app.config reads `.env` relative to the working directory

from app.config import Settings, get_settings  # noqa: E402
from app.paypal.client import PayPalClient, PayPalError  # noqa: E402
from app.paypal.gateway import Gateway  # noqa: E402

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_INTERRUPTED = 130

SMOKE_CIRCLE_ID = "smoke0"  # 6 lowercase alphanumerics, like a real circle id
SECRET_KEY_HINTS = ("token", "secret", "authorization", "password", "client_id", "signature", "cert_url")
# Keys that are ids rather than credentials even though they contain "token" (needed on screen by the operator).
SECRET_KEY_ALLOW = {"setup_token_id", "payment_token_id", "token_id", "vault_id", "token_fetches"}


class SmokeFailure(Exception):
    """An expectation did not hold; the message goes into the final `fail` line."""


def redact(value: Any) -> Any:
    """Recursively replace values under token-like keys with "<redacted>" so no credential reaches stdout."""
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            key = str(k)
            lowered = key.lower()
            if lowered not in SECRET_KEY_ALLOW and any(h in lowered for h in SECRET_KEY_HINTS):
                out[key] = "<redacted>"
            else:
                out[key] = redact(v)
        return out
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def emit(step: str, **fields: Any) -> None:
    """One JSON object per line: {"step": ..., "ts": ..., ...fields}."""
    line = {"step": step, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **redact(fields)}
    print(json.dumps(line, default=str, separators=(",", ":")), flush=True)


def finish(ok: bool, reason: str | None = None) -> None:
    if reason:
        emit("result", ok=ok, reason=reason)
    print("PASS" if ok else "FAIL", flush=True)
    sys.exit(EXIT_PASS if ok else EXIT_FAIL)


def money(value: str) -> Decimal:
    """argparse type for amounts: Decimal with exactly two places, > 0."""
    try:
        dec = Decimal(value).quantize(Decimal("0.01"))
    except Exception as e:  # noqa: BLE001 - argparse wants a ValueError/TypeError
        raise ValueError(f"not a money amount: {value!r}") from e
    if dec <= 0:
        raise ValueError(f"amount must be positive: {value!r}")
    return dec


def settings_or_exit() -> Settings:
    """Settings from `.env`; exit 2 when the sandbox app credentials are missing."""
    try:
        settings = get_settings()
    except Exception as e:  # noqa: BLE001 - a bad .env must become a clean config error
        emit("config", ok=False, error=type(e).__name__, message=str(e))
        print("FAIL", flush=True)
        sys.exit(EXIT_CONFIG)
    if not settings.paypal_configured:
        emit(
            "config",
            ok=False,
            error="NO_CREDENTIALS",
            message="set PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in .env",
        )
        print("FAIL", flush=True)
        sys.exit(EXIT_CONFIG)
    emit("config", ok=True, base_url=settings.paypal_base_url, rate_per_min=settings.paypal_rate_per_min)
    return settings


class SmokeGateway:
    """A gateway + client pair for one script run; records every PayPal call for the final `calls` line."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls: list[dict] = []
        self.client = PayPalClient(settings)
        self.gw = Gateway(self.client, rate_per_min=settings.paypal_rate_per_min, record=self.calls.append)

    async def __aenter__(self) -> SmokeGateway:
        await self.gw.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.gw.stop()
        await self.client.aclose()
        emit(
            "calls",
            count=len(self.calls),
            errors=sum(1 for c in self.calls if c["status"] >= 400 or c["status"] == 0),
            token_fetches=self.client.token_fetches,
            paths=[f"{c['method']} {c['path']} -> {c['status']} ({c['ms']} ms)" for c in self.calls],
        )


def run(main: Callable[[], Awaitable[bool]]) -> None:
    """asyncio.run(main()) with PayPal errors and failed expectations turned into FAIL lines + exit codes."""
    try:
        ok = asyncio.run(main())
    except SmokeFailure as e:
        finish(False, str(e))
    except PayPalError as e:
        emit("paypal_error", ok=False, **e.to_dict())
        finish(False, f"PayPal {e.status} {e.name}")
    except KeyboardInterrupt:
        emit("result", ok=False, reason="interrupted")
        print("FAIL", flush=True)
        sys.exit(EXIT_INTERRUPTED)
    finish(bool(ok))


def wait_for_enter(prompt: str) -> None:
    print(prompt, flush=True)
    try:
        input()
    except EOFError:
        emit("stdin", ok=True, note="stdin closed; continuing without waiting")
