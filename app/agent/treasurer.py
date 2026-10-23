"""The treasurer agent loop (provider-agnostic LLM layer + in-memory MCP tools).

The model is reached through ``app.agent.llm`` (free Groq/Gemini via the OpenAI SDK by default, Anthropic as an
optional switch) and every turn goes through the replay cache (``TK_LLM_CACHE``). Trace events carry
``mode`` "live" | "replay" | "rules".

Anthropic API rules for the configured models (claude-opus-5-5 reasoning / claude-haiku-5-5 text), from the claude-api
skill and PLAN.md section 5: no ``thinking`` parameter (adaptive thinking is always on), no sampling
parameters, ``tool_choice={"type": "auto"}`` only (forced choice returns 400), ``output_config.effort``
set explicitly, strict tools with ``additionalProperties: false``, append-only history (the assistant's
``response.content`` is appended verbatim), every ``stop_reason`` handled.

Fallback: no key and no recording, a rate limit (429), any provider error, a ``refusal``, ``max_tokens``
exhaustion or malformed tool arguments hand the task to ``app.agent.rules_mode.run_rules`` with an honest trace
line ("AI unavailable (rate limit) - rules mode") so the demo works without a model.
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
from app.agent.llm import (
    VOLATILE_KEYS,
    AnthropicProvider,
    LLMProvider,
    LLMUnavailable,
    ToolResult,
    TurnCache,
    _make_anthropic_client,
    cache_key,
    collect_volatile_values,
    get_llm_provider,
    scrub_text,
)
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

# Per-task tool allowlists. The model only sees (and only gets to run) the tools its task needs: a recover run,
# whose context carries member-written text, holds no tool that can charge, pay out or execute remedies.
# execute_remedies and reschedule_turns are never offered to any agent task: Priya's approval executes.
_READ_TOOLS = frozenset({"get_circle", "get_round_matrix", "get_member_history", "record_note"})
RECOVER_TOOLS = _READ_TOOLS | {
    "list_inbox_replies",
    "record_commitment",
    "propose_remedies",
    "open_vote",
    "get_vote_status",
    "request_organizer_approval",
}
NEVER_AGENT_TOOLS = frozenset({"execute_remedies", "reschedule_turns"})
TASK_TOOLS: dict[str, frozenset[str]] = {
    "collect_round": _READ_TOOLS
    | {
        "collect_round",
        "charge_vaulted_member",
        "get_order_details",
        "get_available_balance",
        "create_pot_payout",
        "get_payout_status",
    },
    "payout": _READ_TOOLS
    | {"get_order_details", "get_available_balance", "create_pot_payout", "get_payout_status"},
    "recover": frozenset(RECOVER_TOOLS),
}
TOOL_NOT_ALLOWED = "tool not allowed for this task"


def allowed_tools(kind: str) -> frozenset[str]:
    """Tool names an agent task of this kind may call (never NEVER_AGENT_TOOLS)."""
    return TASK_TOOLS.get(kind, _READ_TOOLS) - NEVER_AGENT_TOOLS


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

    def __init__(self, kind: str, detail: str, *, label: str | None = None, silent: bool = False) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.label = label or kind.replace("_", " ")
        self.silent = silent  # nothing was published yet: run rules mode without a live prefix

    @property
    def reason(self) -> str:
        return self.detail


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
            "Recover round {n}: for the member named in the context (else each member who is not COMPLETED or "
            "COVERED, from get_round_matrix), read list_inbox_replies; if a reply names a payday, "
            "record_commitment. Then propose_remedies (an empty remedies list asks for the charter default), "
            "open_vote and request_organizer_approval, and say what the circle must now decide. Do not "
            "charge anyone again and do not call execute_remedies: people vote, consent and approve first."
        ),
    }
    text = goals[task.kind].format(n=task.round_n)
    # Session-specific values (run_id, ids) never reach the prompt: they would make every session's first turn a
    # replay-cache miss, and no tool needs them.
    ctx = {k: v for k, v in (task.context or {}).items() if k not in VOLATILE_KEYS and v is not None}
    context = json.dumps(ctx, sort_keys=True, default=str) if ctx else "{}"
    return f"Task: {task.kind}\ncircle_id: {task.circle_id}\nround_n: {task.round_n}\n\n{text}\n\nContext: {context}"


# ----------------------------------------------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------------------------------------------

# Kept for callers/tests of M1: the Anthropic client factory now lives in app.agent.llm.
_make_client = _make_anthropic_client

_STOP_LABELS = {"refusal": "refusal", "max_tokens": "max tokens", "error": "malformed model output"}


def _no_llm_reason(settings: Any) -> str:
    if settings.llm_provider_setting == "none":
        return "AI switched off (TK_LLM_PROVIDER=none)"
    return "no LLM key; TK_LLM_API_KEY and ANTHROPIC_API_KEY not set"


async def run_task(
    task: AgentTask,
    *,
    publish: Publish,
    client: Any | None = None,
    provider: LLMProvider | None = None,
    live_allowed: bool = True,
) -> AgentRunResult:
    """Run one treasurer task through the configured LLM provider; fall back to rules mode when it cannot.

    - ``provider``: an ``app.agent.llm.LLMProvider`` (tests pass fakes). ``client``: a pre-built
      ``anthropic.AsyncAnthropic`` (M1 compatibility; wrapped in an ``AnthropicProvider``).
    - Neither given: ``get_llm_provider(settings)``. No key -> a replay-only provider when the cache may
      replay, else rules mode directly.
    - ``live_allowed=False`` (routes: ``allow_live_call(ip)`` said no) -> replay only, then rules mode.
    """
    settings = get_settings()
    owns = provider is None and client is None
    if provider is None and client is not None:
        model = settings.llm_model if settings.llm_provider == "anthropic" else settings.reasoning_model
        provider = AnthropicProvider(model=model, client=client)
    if provider is None:
        provider = get_llm_provider(settings, live=live_allowed)
    if not live_allowed and settings.llm_configured:
        offline_reason = "live AI limit reached for this visitor (TK_LIVE_LLM_PER_IP_HOUR)"
    else:
        offline_reason = _no_llm_reason(settings)
    if provider is None:
        return await rules_mode.run_rules(task, publish=publish, reason=offline_reason)
    if not live_allowed:
        provider.live = False

    cache = TurnCache(settings.llm_cache)
    max_tokens = settings.llm_max_tokens_setting or (MAX_TOKENS if provider.name == "anthropic" else 4096)
    tracer = Tracer(publish, "live")
    try:
        return await _live_loop(
            task,
            provider=provider,
            cache=cache,
            tracer=tracer,
            max_tokens=max_tokens,
            offline_reason=offline_reason,
        )
    except _Fallback as fb:
        if fb.silent:  # nothing was published yet (no key and no recording): a plain rules run
            return await rules_mode.run_rules(task, publish=publish, reason=fb.detail)
        await tracer.emit("system", f"AI unavailable ({fb.label}) - rules mode. {fb.detail}".rstrip())
        result = await rules_mode.run_rules(task, publish=publish, reason=f"AI unavailable ({fb.label})")
        result.trace = [*tracer.events, *result.trace]
        return result
    finally:
        if owns:
            await provider.aclose()


async def _live_loop(
    task: AgentTask,
    *,
    provider: LLMProvider,
    cache: TurnCache,
    tracer: Tracer,
    max_tokens: int,
    offline_reason: str,
) -> AgentRunResult:
    system_text = render_system_prompt(await charter_summary(task.circle_id))
    prompt = task_prompt(task)
    native: list[dict[str, Any]] = provider.user_message(prompt)  # provider format, append-only
    history: list[dict[str, Any]] = [{"role": "user", "text": prompt}]  # provider-neutral, for the cache key
    tool_calls = 0
    final_text = ""
    sources: list[str] = []

    async def announce(source: str, n_tools: int) -> None:
        tracer.mode = source
        if not sources:
            if source == "live":
                await tracer.emit("system", f"Live mode: {provider.label} {provider.model}, {n_tools} tools.")
            else:
                await tracer.emit(
                    "system",
                    f"Replay mode: recorded {provider.label} {provider.model} turns (cache), {n_tools} tools.",
                )
        elif sources[-1] != source:
            msg = (
                "Cache miss: continuing live."
                if source == "live"
                else "Replaying a recorded turn (cache hit)."
            )
            await tracer.emit("system", msg)
        sources.append(source)

    allowed = allowed_tools(task.kind)
    volatile: dict[str, str] = {}  # this run's PayPal ids / run id / receivers -> placeholders (cache scrub)

    with bind_publisher(task.circle_id, tracer.publish):
        async with fastmcp.Client(paypal_ops_internal) as mcp:
            tools = [to_anthropic_tool(t) for t in await mcp.list_tools() if t.name in allowed]

            while True:
                key = cache_key(
                    provider=provider.name,
                    model=provider.model,
                    system=system_text,
                    tools=tools,
                    history=history,
                    circle_id=task.circle_id,
                )
                turn = cache.get(key, circle_id=task.circle_id)
                if turn is not None:
                    await announce("replay", len(tools))
                else:
                    if not provider.live or cache.mode == "replay":
                        raise _Fallback(
                            "replay_miss", offline_reason, label="no recorded turn", silent=not sources
                        )
                    await announce("live", len(tools))
                    try:
                        turn = await provider.chat(system_text, native, tools, max_tokens=max_tokens)
                    except LLMUnavailable as exc:
                        raise _Fallback(exc.kind, exc.detail, label=exc.label) from exc
                    cache.put(
                        key,
                        turn,
                        provider=provider.name,
                        model=provider.model,
                        circle_id=task.circle_id,
                        scrub=volatile,
                    )

                # Append-only history: the provider decides what goes back (Anthropic: content verbatim).
                native.extend(provider.assistant_messages(turn))
                # The key history holds the scrubbed view, exactly what the cache stored, so the next turn's key
                # is the same whether this turn was live (raw ids) or replayed (placeholders).
                history.append(
                    {
                        "role": "assistant",
                        "text": scrub_text(turn.text, volatile),
                        "tool_calls": [
                            {
                                "id": c.id,
                                "name": c.name,
                                "arguments": _scrub_args(c.name, c.arguments, volatile),
                            }
                            for c in turn.tool_calls
                        ],
                    }
                )
                if turn.text:
                    final_text = turn.text
                    await tracer.emit("text", turn.text)

                if turn.stop in _STOP_LABELS:
                    raise _Fallback(
                        turn.stop, turn.detail or f"stop={turn.stop}", label=_STOP_LABELS[turn.stop]
                    )
                if turn.stop != "tool_use" or not turn.tool_calls:
                    break

                if tool_calls + len(turn.tool_calls) > MAX_TOOL_CALLS:
                    await tracer.emit("system", f"Tool call cap ({MAX_TOOL_CALLS}) reached; stopping.")
                    summary = final_text or f"Stopped after {tool_calls} tool calls (cap {MAX_TOOL_CALLS})."
                    return AgentRunResult(
                        ok=False,
                        mode=_run_mode(sources),
                        summary=summary,
                        tool_calls=tool_calls,
                        trace=tracer.events,
                    )

                results: list[ToolResult] = []
                for call in turn.tool_calls:
                    args = dict(call.arguments or {})
                    tool_calls += 1
                    await tracer.emit(
                        "tool_call",
                        f"{call.name}({json.dumps(args, sort_keys=True, default=str)})",
                        tool=call.name,
                        args=args,
                    )
                    t0 = time.perf_counter()
                    if call.name not in allowed:  # never executed, even if the model names a hidden tool
                        result_text = json.dumps({"ok": False, "error": TOOL_NOT_ALLOWED, "tool": call.name})
                        is_error = True
                    else:
                        try:
                            call_result = await mcp.call_tool(call.name, args, raise_on_error=False)
                            _, result_text, is_error = result_payload(call_result)
                        except Exception as exc:  # unknown tool, validation error: tell the model, keep going
                            result_text, is_error = json.dumps({"ok": False, "error": str(exc)}), True
                        collect_volatile_values(result_text, volatile, keep=task.circle_id)
                    ms = int((time.perf_counter() - t0) * 1000)
                    await tracer.emit(
                        "tool_result", truncate(result_text), tool=call.name, ms=ms, is_error=is_error
                    )
                    results.append(
                        ToolResult(id=call.id, name=call.name, content=result_text, is_error=is_error)
                    )
                native.extend(provider.tool_result_messages(results))
                history.append(
                    {
                        "role": "tool",
                        "results": [
                            {"id": r.id, "name": r.name, "content": r.content, "is_error": r.is_error}
                            for r in results
                        ],
                    }
                )

    summary = final_text or "The treasurer finished without a closing summary."
    return AgentRunResult(
        ok=True, mode=_run_mode(sources), summary=summary, tool_calls=tool_calls, trace=tracer.events
    )


def _scrub_args(name: str, args: dict[str, Any] | None, volatile: dict[str, str]) -> dict[str, Any]:
    """Tool arguments as the cache stores them: record_note's free text is scrubbed of run-specific ids."""
    out = dict(args or {})
    if name == "record_note" and isinstance(out.get("text"), str):
        out["text"] = scrub_text(out["text"], volatile)
    return out


def _run_mode(sources: list[str]) -> Literal["live", "replay"]:
    return "replay" if sources and all(s == "replay" for s in sources) else "live"
