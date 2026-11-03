"""paypal-ops PUBLIC: the MCP surface judges can call over HTTP (behind ``app.mcp.auth``). Read-only except
what a member's own answer-scoped capability token does: ``answer_request`` records that member's one answer, and
``get_my_repayment`` may send PayPal's reminder for their invoice once. Nothing moves money.

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
    "View of Turnkeeper demo circles on the PayPal sandbox, read-only except a member's own answer token "
    "(answer_request; get_my_repayment may send PayPal's invoice reminder once). Personal data is "
    "masked. No tool here moves money. A member's own assistant can also use a member capability token (tkc1...): "
    "get_request shows what the member is asked, answer_request records that member's one answer (the "
    "member's own mandate still applies), get_my_repayment shows their PayPal invoice."
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


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _phone_or_date(m: re.Match[str]) -> str:
    """An ISO date ("2027-01-08", an invoice due date) has 8 digits too; it is not a phone number."""
    return m.group(0) if _ISO_DATE_RE.fullmatch(m.group(0)) else "[phone]"


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
        return _PHONE_RE.sub(_phone_or_date, scrubbed)
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


# --- member capability tools (app.domain.capability): one member, one question, 15 minutes -----------------


async def _capability_call(call: Any) -> dict[str, Any]:
    from app.domain.capability import CapabilityError

    try:
        result = await internal._maybe_await(call())
    except CapabilityError as e:
        return e.as_dict()
    except Exception as exc:  # noqa: BLE001 - never raise to the client
        return internal.failure_dict(exc)
    return mask_pii(internal.jsonable(result))


@paypal_ops_public.tool
async def get_request(cap: str, member: str = "") -> dict:
    """What a member is being asked (the open remedy proposal: remedies, round, allowed answers), using that
    member's capability token. ``member`` may be empty; a token works for its own member only."""
    from app.engine import capabilities

    return await _capability_call(lambda: capabilities.request_view(cap, member or None))


@paypal_ops_public.tool
async def answer_request(cap: str, choice: str, rationale: str = "", member: str = "") -> dict:
    """Record the member's one answer ("yes" or "no") to the item their token was issued for. The token is
    single-use and expires after 15 minutes. The member's own signed mandate is a floor: a "yes" it would
    refuse is recorded as "no". ``rationale`` is stored as text and shown; it is never given to a model."""
    from app.engine import capabilities
    from app.engine.sessions import circle_publisher

    async def _answer() -> dict[str, Any]:
        claims = capabilities.open_token(cap, member or None)
        return await capabilities.answer(
            cap, choice, rationale, member=member or None, publish=circle_publisher(claims.circle_id)
        )

    return await _capability_call(_answer)


@paypal_ops_public.tool
async def get_my_repayment(cap: str, member: str = "") -> dict:
    """The member's repayment invoice (amount, due date, status). When the sandbox is live the status is read
    through the PayPal Agent Toolkit (get_invoice). If it is still unpaid after the date the member promised, an
    answer-scoped token sends PayPal's reminder once (send_invoice_reminder); a read token only reports it as due."""
    from app.engine import capabilities

    return await _capability_call(lambda: capabilities.repayment(cap, member or None))


PUBLIC_TOOL_NAMES = (
    "get_circle",
    "get_round_matrix",
    "get_charter",
    "get_payout_status",
    "get_request",
    "answer_request",
    "get_my_repayment",
)
