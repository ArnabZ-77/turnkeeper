"""Rules mode: the deterministic treasurer that needs no LLM.

It walks the SAME MCP tools as the live agent, through the same in-memory ``fastmcp.Client``:
``collect_round`` -> ``get_order_details`` for each COMPLETED member -> ``get_available_balance`` ->
``create_pot_payout`` only when every contribution is COMPLETED or COVERED. Every step publishes a
``trace`` event so the UI shows an honest "rules mode" trace.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import fastmcp

from app.mcp.internal import SETTLED_STATUSES, bind_publisher, paypal_ops_internal

if TYPE_CHECKING:
    from app.agent.treasurer import AgentRunResult, AgentTask

log = logging.getLogger("turnkeeper.agent.rules")

Publish = Callable[[dict], Awaitable[None]]
TRACE_RESULT_CHARS = 400


class Tracer:
    """Publishes ``trace`` events and keeps a copy for ``AgentRunResult.trace``."""

    def __init__(self, publish: Publish | None, mode: str) -> None:
        self.publish = publish
        self.mode = mode
        # Set by the AI loop: the model, and for a replayed turn the date it was recorded (trace row labels).
        self.model: str | None = None
        self.recorded: str | None = None
        self.events: list[dict[str, Any]] = []

    async def emit(self, kind: str, text: str, **extra: Any) -> dict[str, Any]:
        event = {
            "type": "trace",
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "kind": kind,
            "text": text,
            "mode": self.mode,
            **({"model": self.model} if self.model else {}),
            **({"recorded": self.recorded} if self.recorded and self.mode == "replay" else {}),
            **extra,
        }
        self.events.append(event)
        if self.publish is not None:
            try:
                await self.publish(event)
            except Exception as exc:  # the UI stream must never break a run
                log.warning("publish failed: %s", exc)
        return event


def truncate(text: str, limit: int = TRACE_RESULT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def result_payload(result: Any) -> tuple[Any, str, bool]:
    """Normalize a fastmcp ``CallToolResult`` into ``(data, text, is_error)``."""
    data = getattr(result, "data", None)
    if data is None:
        data = getattr(result, "structured_content", None)
    is_error = bool(getattr(result, "is_error", False))
    if data is not None:
        text = json.dumps(data, sort_keys=True, default=str)
    else:
        parts = [getattr(c, "text", "") for c in (getattr(result, "content", None) or [])]
        text = "\n".join(p for p in parts if p) or "{}"
    return data, text, is_error


async def call_tool_traced(client: Any, tracer: Tracer, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call one MCP tool, publish tool_call/tool_result traces, return the result as a dict."""
    await tracer.emit("tool_call", f"{name}({json.dumps(args, sort_keys=True)})", tool=name, args=args)
    t0 = time.perf_counter()
    result = await client.call_tool(name, args, raise_on_error=False)
    ms = int((time.perf_counter() - t0) * 1000)
    data, text, is_error = result_payload(result)
    await tracer.emit("tool_result", truncate(text), tool=name, ms=ms, is_error=is_error)
    if isinstance(data, dict):
        return data
    return {"ok": False, "error": "TOOL_ERROR" if is_error else "UNEXPECTED_RESULT", "message": text}


def _statuses_from_results(results: list[dict[str, Any]]) -> dict[str, str]:
    return {str(r.get("member")): str(r.get("status")) for r in results if r.get("member")}


def _statuses_from_matrix(matrix: dict[str, Any], round_n: int) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for m in matrix.get("members", []) or []:
        if m.get("handle"):
            statuses[str(m["handle"])] = "PENDING"
    for c in matrix.get("contributions", []) or []:
        if int(c.get("round_n", -1)) == round_n and c.get("member"):
            statuses[str(c["member"])] = str(c.get("status"))
    return statuses


def outstanding_members(statuses: dict[str, str]) -> list[str]:
    return sorted(m for m, s in statuses.items() if s not in SETTLED_STATUSES)


async def run_rules(
    task: AgentTask, *, publish: Publish | None = None, reason: str = "no LLM available"
) -> AgentRunResult:
    """Deterministic sequence over the internal MCP tools. Always returns ``mode="rules"``."""
    from app.agent.treasurer import AgentRunResult

    tracer = Tracer(publish, "rules")
    circle_id, round_n = task.circle_id, task.round_n
    await tracer.emit("system", f"Rules mode ({reason}): deterministic treasurer, no model in the loop.")
    tool_calls = 0
    summary_lines: list[str] = []
    ok = True

    async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        nonlocal tool_calls
        tool_calls += 1
        return await call_tool_traced(client, tracer, name, args)

    with bind_publisher(circle_id, publish):
        async with fastmcp.Client(paypal_ops_internal) as client:
            statuses: dict[str, str] = {}

            if task.kind == "collect_round":
                collected = await call("collect_round", {"circle_id": circle_id, "round_n": round_n})
                if not collected.get("ok"):
                    summary_lines.append(f"Collection failed: {_why(collected)}.")
                    ok = False
                else:
                    results = collected.get("results") or []
                    statuses.update(_statuses_from_results(results))
                    completed = [r for r in results if r.get("status") == "COMPLETED"]
                    declined = [r for r in results if r.get("status") == "DECLINED"]
                    summary_lines.append(
                        f"Round {round_n}: {len(completed)} charged, {len(declined)} declined"
                        + (f" ({', '.join(str(r.get('member')) for r in declined)})" if declined else "")
                        + "."
                    )
                    confirmed = 0
                    for r in completed:
                        details = await call(
                            "get_order_details",
                            {"circle_id": circle_id, "member": r["member"], "round_n": round_n},
                        )
                        if details.get("ok"):
                            confirmed += 1
                        else:
                            summary_lines.append(f"Could not confirm {r['member']}: {_why(details)}.")
                    if completed:
                        summary_lines.append(f"Confirmed {confirmed}/{len(completed)} captures with PayPal.")

            if task.kind in ("payout", "recover") or (task.kind == "collect_round" and ok):
                matrix = await call("get_round_matrix", {"circle_id": circle_id})
                if matrix.get("ok"):
                    for member, status in _statuses_from_matrix(matrix, round_n).items():
                        statuses.setdefault(member, status)
                        if statuses.get(member) != status and status in SETTLED_STATUSES:
                            statuses[member] = status
                elif not statuses:
                    summary_lines.append(f"Could not read the round matrix: {_why(matrix)}.")
                    ok = False

            if task.kind == "recover":
                for member in outstanding_members(statuses):
                    history = await call("get_member_history", {"circle_id": circle_id, "member": member})
                    late = history.get("late_rounds") if history.get("ok") else None
                    rule = history.get("payday_rule") if history.get("ok") else None
                    summary_lines.append(
                        f"{member} is outstanding (late rounds: {late or 'none'}; payday rule: {rule or 'unknown'})."
                        " Remedies need a human decision in this version."
                    )

            if ok and task.kind in ("collect_round", "payout"):
                balance = await call("get_available_balance", {"circle_id": circle_id})
                if balance.get("ok"):
                    summary_lines.append(
                        f"Balance check: PayPal available {balance.get('paypal_available') or balance.get('paypal_balance_label') or 'n/a'}, "
                        f"circle available {balance.get('circle_available')} (source: {balance.get('source')})."
                    )
                else:
                    summary_lines.append(f"Balance check failed: {_why(balance)}.")
                pending = outstanding_members(statuses)
                if not statuses:
                    summary_lines.append("Payout held: no contribution data for this round.")
                    ok = False
                elif pending:
                    summary_lines.append(f"Payout held: waiting on {', '.join(pending)}.")
                elif not balance.get("ok"):
                    summary_lines.append("Payout held: balance unknown.")
                    ok = False
                else:
                    payout = await call("create_pot_payout", {"circle_id": circle_id, "round_n": round_n})
                    if payout.get("ok"):
                        summary_lines.append(
                            f"Pot paid: {payout.get('amount')} to {payout.get('recipient')} via "
                            f"{payout.get('wallet')} (status {payout.get('status')}, item {payout.get('item_id')})."
                        )
                    else:
                        summary_lines.append(f"Payout not sent: {_why(payout)}.")
                        ok = False

            if summary_lines:
                await call("record_note", {"circle_id": circle_id, "text": " ".join(summary_lines)})

    summary = " ".join(summary_lines) or "Nothing to do."
    await tracer.emit("text", summary)
    return AgentRunResult(ok=ok, mode="rules", summary=summary, tool_calls=tool_calls, trace=tracer.events)


def _why(result: dict[str, Any]) -> str:
    if result.get("denied"):
        return f"denied by policy: {result['denied']}"
    label = result.get("label") or result.get("message")
    error = result.get("error") or "unknown error"
    return f"{error}" + (f" ({label})" if label else "")
