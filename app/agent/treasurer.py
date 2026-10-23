"""The treasurer agent loop (Anthropic Messages API + in-memory MCP tools).

API rules for the configured models (claude-opus-5-5 reasoning / claude-haiku-5-5 text), from the claude-api
skill and PLAN.md section 5: no ``thinking`` parameter (adaptive thinking is always on), no sampling
parameters, ``tool_choice={"type": "auto"}`` only (forced choice returns 400), ``output_config.effort``
set explicitly, strict tools with ``additionalProperties: false``, append-only history (the assistant's
``response.content`` is appended verbatim), every ``stop_reason`` handled.

Fallback: a missing API key, a ``refusal``, ``max_tokens`` exhaustion or any ``anthropic.APIError`` hands the
task to ``app.agent.rules_mode.run_rules`` so the demo works without a model.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import fastmcp
from pydantic import BaseModel, Field

from app.agent import rules_mode
from app.agent.rules_mode import Tracer, result_payload, truncate
from app.config import get_settings
from app.mcp.internal import bind_publisher, paypal_ops_internal
from app.mcp.strict_schema import to_anthropic_tool

log = logging.getLogger("turnkeeper.agent.treasurer")

Publish = Callable[[dict], Awaitable[None]]

MAX_TOOL_CALLS = 25
MAX_TOKENS = 8000
EFFORT = "medium"
PROMPT_PATH = Path(__file__).parent / "prompts" / "treasurer.md"
_TERMINAL_STOPS = {"end_turn", "stop_sequence", "pause_turn"}
_FALLBACK_STOPS = {"refusal", "max_tokens", "model_context_window_exceeded"}


class AgentTask(BaseModel):
    kind: Literal["collect_round", "payout", "recover"]
    circle_id: str
    round_n: int
    sid: str
    context: dict = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    ok: bool
    mode: Literal["live", "replay", "rules"]
    summary: str
    tool_calls: int
    trace: list[dict]


class _Fallback(Exception):
    """Internal signal: leave the live loop and run rules mode with this reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ----------------------------------------------------------------------------------------------------------
# Prompt assembly
# ----------------------------------------------------------------------------------------------------------


def load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def render_system_prompt(charter_summary: str) -> str:
    return load_prompt_template().replace(
        "{{charter_summary}}", charter_summary.strip() or "(charter unavailable)"
    )


async def charter_summary(circle_id: str) -> str:
    """Plain-text charter summary for the system prompt. Degrades gracefully when the engine is missing."""
    try:
        import importlib

        state = importlib.import_module("app.engine.state")
        charter = state.load_charter(circle_id)
        if hasattr(charter, "__await__"):
            charter = await charter
    except Exception as exc:
        log.info("charter unavailable for %s: %s", circle_id, exc)
        return "Charter not loaded; read it with get_circle before acting."
    members = ", ".join(
        f"{m.handle} ({m.name.split()[0] if m.name else m.handle}, {m.lang}, {m.payout_wallet})"
        for m in charter.members
    )
    lines = [
        f"- Circle: {charter.name} ({circle_id}); currency {charter.currency}",
        f"- Contribution: {charter.amount} per member per round, {charter.cadence}, first due {charter.start_date}",
        f"- Members ({len(charter.members)}): {members}",
        f"- Rotation (payout order): {', '.join(charter.rotation)}",
        f"- Fee policy: {charter.fee_policy}; buffer seed {charter.buffer.seed} ({charter.buffer.source}); "
        f"grace {charter.grace_days} days; quorum {charter.quorum}",
        f"- Remedy menu: {', '.join(charter.remedy_menu)}",
    ]
    try:
        import importlib

        domain = importlib.import_module("app.domain.charter")
        lines.append(f"- Charter sha256: {domain.charter_sha256(charter)}")
    except Exception:
        pass
    return "\n".join(lines)


def task_prompt(task: AgentTask) -> str:
    goals = {
        "collect_round": (
            "Collect round {n}: charge every pending member with collect_round, confirm each COMPLETED "
            "contribution with get_order_details, check get_available_balance, and pay the pot with "
            "create_pot_payout only if every contribution is COMPLETED or COVERED. Otherwise hold it and "
            "explain which members are outstanding."
        ),
        "payout": (
            "Pay out round {n}: verify with get_round_matrix that every contribution is COMPLETED or COVERED, "
            "check get_available_balance, then create_pot_payout. If anything is outstanding, hold and explain."
        ),
        "recover": (
            "Recover round {n}: read get_round_matrix and get_member_history for each member who is not "
            "COMPLETED or COVERED, then write a record_note that states what is outstanding and what the "
            "organizer should decide. Do not charge anyone again."
        ),
    }
    text = goals[task.kind].format(n=task.round_n)
    context = json.dumps(task.context, sort_keys=True, default=str) if task.context else "{}"
    return f"Task: {task.kind}\ncircle_id: {task.circle_id}\nround_n: {task.round_n}\n\n{text}\n\nContext: {context}"


# ----------------------------------------------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------------------------------------------


def _make_client(api_key: str) -> Any:
    import anthropic

    return anthropic.AsyncAnthropic(api_key=api_key)


def _text_of(response: Any) -> str:
    return "\n".join(
        b.text for b in response.content if getattr(b, "type", None) == "text" and b.text
    ).strip()


async def run_task(task: AgentTask, *, publish: Publish, client: Any | None = None) -> AgentRunResult:
    """Run one treasurer task live; fall back to rules mode when the model cannot be used.

    ``client`` is an optional pre-built ``anthropic.AsyncAnthropic`` (tests pass a fake). When omitted and
    ``settings.anthropic_api_key`` is empty, rules mode runs directly.
    """
    settings = get_settings()
    if client is None and not settings.anthropic_api_key:
        return await rules_mode.run_rules(task, publish=publish, reason="ANTHROPIC_API_KEY not set")

    tracer = Tracer(publish, "live")
    owns_client = client is None
    if owns_client:
        client = _make_client(settings.anthropic_api_key)
    try:
        return await _live_loop(task, client=client, tracer=tracer, model=settings.reasoning_model)
    except _Fallback as fb:
        await tracer.emit("system", f"Falling back to rules mode: {fb.reason}")
        result = await rules_mode.run_rules(task, publish=publish, reason=fb.reason)
        result.trace = [*tracer.events, *result.trace]
        return result
    finally:
        if owns_client and hasattr(client, "close"):
            try:
                await client.close()
            except Exception:
                pass


async def _live_loop(task: AgentTask, *, client: Any, tracer: Tracer, model: str) -> AgentRunResult:
    import anthropic

    system_text = render_system_prompt(await charter_summary(task.circle_id))
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [{"role": "user", "content": task_prompt(task)}]
    tool_calls = 0
    final_text = ""

    with bind_publisher(task.circle_id, tracer.publish):
        async with fastmcp.Client(paypal_ops_internal) as mcp:
            tools = [to_anthropic_tool(t) for t in await mcp.list_tools()]
            await tracer.emit("system", f"Live mode: {model}, {len(tools)} strict tools.")

            while True:
                try:
                    response = await client.messages.create(
                        model=model,
                        max_tokens=MAX_TOKENS,
                        system=system,
                        tools=tools,
                        tool_choice={"type": "auto"},
                        output_config={"effort": EFFORT},
                        messages=messages,
                    )
                except anthropic.APIError as exc:
                    log.warning("anthropic API error: %s", exc)
                    raise _Fallback(f"Anthropic API error: {type(exc).__name__}") from exc

                # Append-only history: the assistant turn goes in verbatim (thinking blocks included).
                messages.append({"role": "assistant", "content": response.content})
                text = _text_of(response)
                if text:
                    final_text = text
                    await tracer.emit("text", text)

                stop = response.stop_reason
                if stop in _FALLBACK_STOPS:
                    details = getattr(response, "stop_details", None)
                    why = f"stop_reason={stop}"
                    if details is not None:
                        why += f" ({getattr(details, 'category', None) or ''} {getattr(details, 'explanation', None) or ''})".rstrip()
                    raise _Fallback(why)

                tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
                if stop != "tool_use" or not tool_uses:
                    if stop not in _TERMINAL_STOPS and stop is not None:
                        log.info("unexpected stop_reason %s treated as end of turn", stop)
                    break

                if tool_calls + len(tool_uses) > MAX_TOOL_CALLS:
                    await tracer.emit("system", f"Tool call cap ({MAX_TOOL_CALLS}) reached; stopping.")
                    summary = final_text or f"Stopped after {tool_calls} tool calls (cap {MAX_TOOL_CALLS})."
                    return AgentRunResult(
                        ok=False, mode="live", summary=summary, tool_calls=tool_calls, trace=tracer.events
                    )

                # Every tool_use in this turn gets its tool_result in ONE user message.
                results: list[dict[str, Any]] = []
                for block in tool_uses:
                    args = dict(block.input or {})
                    tool_calls += 1
                    await tracer.emit(
                        "tool_call",
                        f"{block.name}({json.dumps(args, sort_keys=True, default=str)})",
                        tool=block.name,
                        args=args,
                    )
                    t0 = time.perf_counter()
                    try:
                        call_result = await mcp.call_tool(block.name, args, raise_on_error=False)
                        _, result_text, is_error = result_payload(call_result)
                    except Exception as exc:  # unknown tool, validation error: tell the model, keep going
                        result_text, is_error = json.dumps({"ok": False, "error": str(exc)}), True
                    ms = int((time.perf_counter() - t0) * 1000)
                    await tracer.emit(
                        "tool_result", truncate(result_text), tool=block.name, ms=ms, is_error=is_error
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                            "is_error": is_error,
                        }
                    )
                messages.append({"role": "user", "content": results})

    summary = final_text or "The treasurer finished without a closing summary."
    return AgentRunResult(ok=True, mode="live", summary=summary, tool_calls=tool_calls, trace=tracer.events)
