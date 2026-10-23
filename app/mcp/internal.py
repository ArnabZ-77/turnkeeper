"""paypal-ops INTERNAL: the full tool registry the treasurer agent uses in-process.

``paypal_ops_internal`` is a ``FastMCP`` server that is never mounted over HTTP. The agent loop and the
rules-mode fallback both reach it through ``fastmcp.Client(paypal_ops_internal)``. Tools take logical
handles (``circle_id``, ``round_n``, member ``handle``) and delegate to B4's engine
(``app.engine.rounds`` / ``app.engine.state``), imported lazily so this module imports on a bare checkout.

Error contract (docs/CONTRACTS.md, B3): tools never raise to the model.
``PolicyDenied`` -> ``{"ok": false, "denied": reason}``; ``PayPalError`` ->
``{"ok": false, "error": name, "issues": [...], "label": "..."}``; anything else ->
``{"ok": false, "error": <ExceptionName>, "message": ...}``. Success -> ``{"ok": true, ...}``.
"""

from __future__ import annotations

import datetime as dt
import decimal
import importlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any

from fastmcp import FastMCP

from app.agent.handles import validate_inputs

log = logging.getLogger("turnkeeper.mcp.internal")

Publish = Callable[[dict], Awaitable[None]]

SERVER_NAME = "paypal-ops"
INSTRUCTIONS = (
    "Turnkeeper treasurer tools for one rotating savings circle on the PayPal sandbox. Inputs are logical "
    "handles (circle_id, round_n, member handle), never raw PayPal IDs. Money tools are gated by the policy "
    "engine and return {ok: false, denied: ...} instead of raising."
)

paypal_ops_internal = FastMCP(name=SERVER_NAME, instructions=INSTRUCTIONS)

SETTLED_STATUSES = frozenset({"COMPLETED", "COVERED"})
CONTRIBUTION_KEYS = (
    "round_n",
    "member",
    "status",
    "order_id",
    "capture_id",
    "gross",
    "fee",
    "net",
    "decline_code",
    "decline_source",
    "attempt",
    "webhook_verified",
    "predicted",
    "label",
)
MEMBER_KEYS = ("handle", "name", "lang", "payout_wallet", "vaulted", "rotation_pos")
ROUND_KEYS = ("n", "due_at", "recipient", "status", "payout", "paid_at")
NOTE_MAX_CHARS = 2000

# ----------------------------------------------------------------------------------------------------------
# Publisher binding: the engine publishes SSE events through the session's `publish`; tools look it up by
# circle id because the in-memory MCP session may run in a different task than the caller.
# ----------------------------------------------------------------------------------------------------------

_PUBLISHERS: dict[str, Publish] = {}


async def _noop_publish(event: dict) -> None:
    return None


def publisher_for(circle_id: str) -> Publish:
    return _PUBLISHERS.get(circle_id, _noop_publish)


@contextmanager
def bind_publisher(circle_id: str, publish: Publish | None) -> Iterator[None]:
    """Route engine events for ``circle_id`` to ``publish`` while the block runs."""
    previous = _PUBLISHERS.get(circle_id)
    _PUBLISHERS[circle_id] = publish or _noop_publish
    try:
        yield
    finally:
        if previous is None:
            _PUBLISHERS.pop(circle_id, None)
        else:
            _PUBLISHERS[circle_id] = previous


# ----------------------------------------------------------------------------------------------------------
# Lazy engine access and result shaping
# ----------------------------------------------------------------------------------------------------------


def _rounds() -> ModuleType:
    return importlib.import_module("app.engine.rounds")


def _state() -> ModuleType:
    return importlib.import_module("app.engine.state")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return f"{value:.2f}" if value == value.quantize(decimal.Decimal("0.01")) else str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value, key=str)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def jsonable(value: Any) -> Any:
    """Round-trip through JSON so Decimals become "200.00" strings and dates become ISO strings."""
    return json.loads(json.dumps(value, default=_json_default))


def _exception_matches(exc: BaseException, module: str, class_name: str) -> bool:
    """isinstance check against a lazily imported class, with a name-based fallback for stand-ins."""
    try:
        cls = getattr(importlib.import_module(module), class_name)
    except Exception:
        cls = None
    if cls is not None and isinstance(exc, cls):
        return True
    return type(exc).__name__ == class_name


def failure_dict(exc: BaseException) -> dict[str, Any]:
    """Map an engine/PayPal/policy exception onto the tool error contract."""
    if _exception_matches(exc, "app.domain.policy", "PolicyDenied"):
        reason = getattr(exc, "reason", None) or str(exc) or "denied by policy"
        return {"ok": False, "denied": str(reason)}
    if _exception_matches(exc, "app.paypal.client", "PayPalError"):
        name = getattr(exc, "name", None) or "PAYPAL_ERROR"
        issues = getattr(exc, "issue_codes", None)
        if issues is None:
            issues = [d.get("issue") for d in (getattr(exc, "details", None) or []) if isinstance(d, dict)]
        issues = [i for i in (issues or []) if i]
        status = getattr(exc, "status", None)
        message = getattr(exc, "message", None) or str(exc)
        label = f"PayPal {status} {name}" if status else f"PayPal {name}"
        if "INSTRUMENT_DECLINED" in issues:
            label = "API decline: INSTRUMENT_DECLINED"
        elif message:
            label = f"{label}: {message}"
        out: dict[str, Any] = {"ok": False, "error": str(name), "issues": issues, "label": label}
        debug_id = getattr(exc, "debug_id", None)
        if debug_id:
            out["debug_id"] = debug_id
        return out
    if isinstance(exc, ModuleNotFoundError | ImportError):
        return {"ok": False, "error": "ENGINE_UNAVAILABLE", "message": str(exc)}
    return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


def ok_dict(result: Any, **extra: Any) -> dict[str, Any]:
    """Shape an engine result as ``{"ok": true, ...}`` with ``ok`` first."""
    if isinstance(result, dict):
        body = {k: v for k, v in result.items() if k != "ok"}
        ok = bool(result.get("ok", True))
    elif isinstance(result, list):
        body, ok = {"results": result}, True
    elif result is None:
        body, ok = {}, True
    else:
        body, ok = {"value": result}, True
    body.update(extra)
    return jsonable({"ok": ok, **body})


async def _guarded(call: Callable[[], Awaitable[Any] | Any], **extra: Any) -> dict[str, Any]:
    try:
        result = await _maybe_await(call())
    except Exception as exc:  # never raise to the model
        log.warning("tool failure: %s: %s", type(exc).__name__, exc)
        return jsonable(failure_dict(exc))
    return ok_dict(result, **extra)


def _invalid(problem: str) -> dict[str, Any]:
    return {"ok": False, "error": "INVALID_INPUT", "message": problem}


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: row.get(k) for k in keys if k in row}


def _queue_position() -> int:
    try:
        gateway = importlib.import_module("app.paypal.gateway")
        return int(gateway.get_gateway().position())
    except Exception:
        return 0


# ----------------------------------------------------------------------------------------------------------
# Shared implementations (also used by the public, read-only server)
# ----------------------------------------------------------------------------------------------------------


async def board_for(circle_id: str) -> dict[str, Any]:
    """The BoardModel for a circle (engine ``board_model``), as JSON-safe data."""
    board = await _maybe_await(_state().board_model(circle_id))
    return jsonable(board)


async def round_matrix_for(circle_id: str) -> dict[str, Any]:
    board = await board_for(circle_id)
    return {
        "members": [_pick(m, MEMBER_KEYS) for m in board.get("members", [])],
        "rounds": [_pick(r, ROUND_KEYS) for r in board.get("rounds", [])],
        "contributions": [_pick(c, CONTRIBUTION_KEYS) for c in board.get("contributions", [])],
    }


async def member_history_for(circle_id: str, member: str) -> dict[str, Any]:
    state = _state()
    custom = getattr(state, "member_history", None)
    if callable(custom):
        return jsonable(await _maybe_await(custom(circle_id, member)))
    board = await board_for(circle_id)
    handles = {m.get("handle") for m in board.get("members", [])}
    if member not in handles:
        raise KeyError(f"unknown member handle {member!r}")
    late_rounds: list[int] = []
    for c in board.get("contributions", []):
        if c.get("member") != member:
            continue
        if c.get("status") == "DECLINED" or (c.get("attempt") or 1) > 1 or c.get("status") == "COVERED":
            late_rounds.append(int(c["round_n"]))
    payday_rule = None
    try:
        charter = await _maybe_await(state.load_charter(circle_id))
        for m in getattr(charter, "members", []):
            if getattr(m, "handle", None) == member:
                payday_rule = getattr(m, "payday_rule", None)
    except Exception:
        payday_rule = next(
            (m.get("payday_rule") for m in board.get("members", []) if m.get("handle") == member), None
        )
    return {
        "member": member,
        "late_rounds": sorted(set(late_rounds)),
        "payday_rule": payday_rule,
        "commitments": [],
        "replies": [],
    }


async def payout_status_for(circle_id: str, round_n: int) -> dict[str, Any]:
    return jsonable(await _maybe_await(_rounds().payout_status(circle_id, round_n)))


# ----------------------------------------------------------------------------------------------------------
# Tools (M1 list from docs/CONTRACTS.md)
# ----------------------------------------------------------------------------------------------------------


@paypal_ops_internal.tool
async def get_circle(circle_id: str) -> dict:
    """Read the whole circle board: charter summary, members, rounds, contributions, counters and ticker.
    Start here. Statuses: contributions PENDING|COMPLETED|DECLINED|COVERED; rounds PENDING|COLLECTING|
    COLLECTED|PAID|VOID."""
    if problem := validate_inputs(circle_id=circle_id):
        return _invalid(problem)
    return await _guarded(lambda: board_for(circle_id))


@paypal_ops_internal.tool
async def get_round_matrix(circle_id: str) -> dict:
    """Compact members x rounds view: members, rounds and every contribution with its status, order and
    capture ids, decline code, attempt counter and webhook_verified flag."""
    if problem := validate_inputs(circle_id=circle_id):
        return _invalid(problem)
    return await _guarded(lambda: round_matrix_for(circle_id))


@paypal_ops_internal.tool
async def get_member_history(circle_id: str, member: str) -> dict:
    """One member's track record in this circle: late_rounds, payday_rule, commitments and replies.
    Use it before proposing a remedy for a declined member."""
    if problem := validate_inputs(circle_id=circle_id, member=member):
        return _invalid(problem)
    return await _guarded(lambda: member_history_for(circle_id, member))


@paypal_ops_internal.tool
async def collect_round(circle_id: str, round_n: int) -> dict:
    """MONEY. Charge every member's vaulted PayPal agreement for one round (policy-gated, one success per
    member per round, idempotent request ids). Returns results[] with member, status, order_id, capture_id,
    decline_code and label, plus the gateway queued_position."""
    if problem := validate_inputs(circle_id=circle_id, round_n=round_n):
        return _invalid(problem)
    publish = publisher_for(circle_id)
    out = await _guarded(lambda: _rounds().collect_round(circle_id, round_n, publish=publish))
    if out.get("ok"):
        out.setdefault("results", [])
        out["queued_position"] = _queue_position()
    return out


@paypal_ops_internal.tool
async def charge_vaulted_member(circle_id: str, round_n: int, member: str) -> dict:
    """MONEY. Charge a single member for one round (policy-gated: exactly the charter amount, at most two
    retries per member per round). Returns one result: member, status, order_id, capture_id, decline_code,
    label."""
    if problem := validate_inputs(circle_id=circle_id, member=member, round_n=round_n):
        return _invalid(problem)
    publish = publisher_for(circle_id)
    return await _guarded(lambda: _rounds().charge_member(circle_id, round_n, member, publish=publish))


@paypal_ops_internal.tool
async def get_order_details(circle_id: str, member: str, round_n: int) -> dict:
    """Confirm a member's contribution order with the PayPal Agent Toolkit (get_order_details): returns the
    order status and capture id as PayPal reports them. Call it for every COMPLETED contribution before
    counting the money."""
    if problem := validate_inputs(circle_id=circle_id, member=member, round_n=round_n):
        return _invalid(problem)
    publish = publisher_for(circle_id)
    return await _guarded(lambda: _rounds().confirm_capture(circle_id, round_n, member, publish=publish))


@paypal_ops_internal.tool
async def get_available_balance(circle_id: str) -> dict:
    """Funds check before a payout: paypal_available (account-wide, from Reporting/Balances; null when the
    API has no figure), circle_available (this circle's net ledger) and source ("balances" or "ledger")."""
    if problem := validate_inputs(circle_id=circle_id):
        return _invalid(problem)
    return await _guarded(lambda: _rounds().available_balance(circle_id))


@paypal_ops_internal.tool
async def create_pot_payout(circle_id: str, round_n: int) -> dict:
    """MONEY. Pay the round's pot to the scheduled recipient through PayPal Payouts. Gated by the policy
    engine and the organizer-approved mandate: every contribution must be COMPLETED or COVERED, the ledger
    and the balance must cover the amount, exactly one live payout per round. Returns batch_id, item_id,
    status, amount, recipient and wallet."""
    if problem := validate_inputs(circle_id=circle_id, round_n=round_n):
        return _invalid(problem)
    publish = publisher_for(circle_id)
    return await _guarded(lambda: _rounds().pay_round(circle_id, round_n, publish=publish))


@paypal_ops_internal.tool
async def get_payout_status(circle_id: str, round_n: int) -> dict:
    """Current PayPal Payouts status for a round's pot: status (PayPal's own string, e.g. SUCCESS,
    PENDING, UNCLAIMED), item_id and batch_id."""
    if problem := validate_inputs(circle_id=circle_id, round_n=round_n):
        return _invalid(problem)
    return await _guarded(lambda: payout_status_for(circle_id, round_n))


@paypal_ops_internal.tool
async def record_note(circle_id: str, text: str) -> dict:
    """Write a short note to the organizer's trace (the only free-text sink). Use it for anything the
    organizer should read: what was confirmed, why a payout was held, what needs a human decision."""
    if problem := validate_inputs(circle_id=circle_id):
        return _invalid(problem)
    note = (text or "").strip()[:NOTE_MAX_CHARS]
    if not note:
        return _invalid("note text is empty")
    event = {
        "type": "trace",
        "ts": dt.datetime.now(dt.UTC).isoformat(),
        "kind": "text",
        "text": note,
        "source": "note",
    }
    try:
        await publisher_for(circle_id)(event)
    except Exception as exc:  # a broken publisher must not fail the tool
        log.warning("record_note publish failed: %s", exc)
    return {"ok": True, "chars": len(note)}


TOOL_NAMES = (
    "get_circle",
    "get_round_matrix",
    "get_member_history",
    "collect_round",
    "charge_vaulted_member",
    "get_order_details",
    "get_available_balance",
    "create_pot_payout",
    "get_payout_status",
    "record_note",
)
MONEY_TOOLS = frozenset({"collect_round", "charge_vaulted_member", "create_pot_payout"})
