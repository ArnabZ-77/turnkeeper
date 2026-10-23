"""paypal-ops PUBLIC: the read-only MCP surface judges can call over HTTP (behind ``app.mcp.auth``).

Only demo circles (``Circle.is_fixture_clone``) are visible, and every response is PII-masked: names are
reduced to first names, emails and phone numbers are removed, vault/customer/sandbox identifiers are
dropped. The engine is reached through the same implementations as the internal server.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import re
from typing import Any

from fastmcp import FastMCP

from app.agent.handles import first_name, validate_inputs
from app.mcp import internal

log = logging.getLogger("turnkeeper.mcp.public")

SERVER_NAME = "paypal-ops-public"
INSTRUCTIONS = (
    "Read-only view of Turnkeeper demo circles on the PayPal sandbox. Personal data is masked. "
    "No tool here moves money."
)

paypal_ops_public = FastMCP(name=SERVER_NAME, instructions=INSTRUCTIONS)

DROP_KEYS = frozenset(
    {
        "email",
        "emails",
        "phone",
        "phone_number",
        "payout_receiver",
        "receiver",
        "payer_email",
        "sandbox_account",
        "vault_id",
        "customer_id",
        "payer_id",
        "address",
    }
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])")


def mask_pii(value: Any) -> Any:
    """Recursively drop PII keys, reduce ``name`` fields to first names and scrub emails/phones in text."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in DROP_KEYS:
                continue
            if k == "name" and isinstance(v, str):
                out[k] = first_name(v)
            else:
                out[k] = mask_pii(v)
        return out
    if isinstance(value, list):
        return [mask_pii(v) for v in value]
    if isinstance(value, str):
        scrubbed = _EMAIL_RE.sub("[email]", value)
        return _PHONE_RE.sub("[phone]", scrubbed)
    return value


async def is_demo_circle(circle_id: str) -> bool:
    """True only for fixture clones (demo circles). Fails closed on any error or missing module."""
    try:
        state = importlib.import_module("app.engine.state")
        hook = getattr(state, "is_fixture_clone", None) or getattr(state, "is_demo_circle", None)
        if callable(hook):
            result = hook(circle_id)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
    except Exception as exc:
        log.debug("demo-circle hook unavailable: %s", exc)

    def _db_lookup() -> bool:
        db = importlib.import_module("app.db")
        models = importlib.import_module("app.models")
        with db.session_scope() as session:
            row = session.get(models.Circle, circle_id)
            return bool(row is not None and getattr(row, "is_fixture_clone", False))

    try:
        return await asyncio.to_thread(_db_lookup)
    except Exception as exc:
        log.debug("demo-circle db lookup failed: %s", exc)
    try:
        board = await internal.board_for(circle_id)
        flag = (board.get("circle") or {}).get("is_fixture_clone")
        return bool(flag)
    except Exception:
        return False


def _not_demo(circle_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "NOT_A_DEMO_CIRCLE",
        "message": f"circle {circle_id!r} is not a demo circle",
    }


async def _public_call(circle_id: str, call: Any, **extra: Any) -> dict[str, Any]:
    if not await is_demo_circle(circle_id):
        return _not_demo(circle_id)
    result = await internal._guarded(call, **extra)
    return mask_pii(result)


@paypal_ops_public.tool
async def get_circle(circle_id: str) -> dict:
    """Read a demo circle's board (members as first names, rounds, contributions, counters). Read-only."""
    if problem := validate_inputs(circle_id=circle_id):
        return internal._invalid(problem)
    return await _public_call(circle_id, lambda: internal.board_for(circle_id))


@paypal_ops_public.tool
async def get_round_matrix(circle_id: str) -> dict:
    """Members x rounds matrix of a demo circle with contribution statuses and PayPal order/capture ids."""
    if problem := validate_inputs(circle_id=circle_id):
        return internal._invalid(problem)
    return await _public_call(circle_id, lambda: internal.round_matrix_for(circle_id))


@paypal_ops_public.tool
async def get_charter(circle_id: str) -> dict:
    """The frozen charter of a demo circle (amount, cadence, rotation, fee policy, buffer, quorum, remedy
    menu, sha256). Member contact details are masked."""
    if problem := validate_inputs(circle_id=circle_id):
        return internal._invalid(problem)

    async def _charter() -> dict[str, Any]:
        state = importlib.import_module("app.engine.state")
        charter = await internal._maybe_await(state.load_charter(circle_id))
        data = charter.model_dump(mode="json") if hasattr(charter, "model_dump") else dict(charter)
        try:
            domain = importlib.import_module("app.domain.charter")
            data["charter_sha256"] = domain.charter_sha256(charter)
        except Exception:
            pass
        return data

    return await _public_call(circle_id, _charter)


@paypal_ops_public.tool
async def get_payout_status(circle_id: str, round_n: int) -> dict:
    """PayPal Payouts status for one round of a demo circle: status, item_id, batch_id."""
    if problem := validate_inputs(circle_id=circle_id, round_n=round_n):
        return internal._invalid(problem)
    return await _public_call(circle_id, lambda: internal.payout_status_for(circle_id, round_n))


PUBLIC_TOOL_NAMES = ("get_circle", "get_round_matrix", "get_charter", "get_payout_status")
