"""Provider-agnostic LLM layer: one small interface, two implementations, a replay cache and a live-call limiter.

- ``LLMProvider.chat(system, messages, tools, *, max_tokens) -> LLMTurn`` is the only call the agent makes.
  ``messages`` are in the provider's native format; the provider builds them for the loop through
  ``user_message`` / ``assistant_messages`` / ``tool_result_messages`` so the loop stays format-free.
- ``AnthropicProvider`` keeps the M1 Anthropic rules (no ``thinking`` / sampling params, ``tool_choice`` auto,
  strict tools, ``output_config.effort``, assistant ``response.content`` appended verbatim, refusals handled).
- ``OpenAICompatProvider`` drives any OpenAI-compatible endpoint with the official ``openai`` SDK: the free
  defaults are Groq (``openai/gpt-oss-120b``) and Google AI Studio (Gemini's OpenAI endpoint).
- ``TurnCache`` stores model turns in ``LlmCache`` keyed by a hash of the normalized request, so a recorded run
  replays without any key (``TK_LLM_CACHE``: off | record | replay | auto).
- ``allow_live_call(client_ip)`` is an in-memory sliding window (``TK_LIVE_LLM_PER_IP_HOUR`` live runs per IP per
  hour) that routes consult before letting a run call a live model.

Free tiers may use prompts for training (Gemini): only synthetic demo data and logical handles reach a prompt.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import Settings, get_settings

log = logging.getLogger("turnkeeper.agent.llm")

Stop = Literal["tool_use", "end", "max_tokens", "refusal", "error"]
EFFORT = "medium"  # Anthropic output_config.effort (M1 value)

PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "groq": "Groq",
    "openai_compat": "OpenAI-compatible",
}


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Provider-specific fields that must travel back with the call (e.g. Gemini's ``extra_content``
    # thought signature on the OpenAI endpoint). Opaque to the loop.
    extra: dict[str, Any] = Field(default_factory=dict)


class LLMTurn(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop: Stop = "end"
    raw_assistant: Any = None  # opaque: what the provider wants appended back to the history
    usage: dict[str, Any] = Field(default_factory=dict)
    detail: str = ""  # human-readable reason for max_tokens / refusal / error stops


@dataclass
class ToolResult:
    id: str
    name: str
    content: str
    is_error: bool = False


_UNAVAILABLE_LABELS = {
    "rate_limit": "rate limit",
    "auth": "API key rejected",
    "unavailable": "provider unreachable",
    "no_key": "no API key",
    "replay_miss": "no recorded turn",
    "error": "provider error",
}


class LLMUnavailable(Exception):
    """The model cannot be used for this turn. ``kind`` picks the honest trace label."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail

    @property
    def label(self) -> str:
        return _UNAVAILABLE_LABELS.get(self.kind, self.kind)


# ----------------------------------------------------------------------------------------------------------
# Provider interface
# ----------------------------------------------------------------------------------------------------------


class LLMProvider:
    """Base class. ``live=False`` makes a replay-only provider (formats messages, never calls the network)."""

    name: str = "none"

    def __init__(self, *, model: str, live: bool = True) -> None:
        self.model = model
        self.live = live
        self._owns_client = False

    @property
    def label(self) -> str:
        return PROVIDER_LABELS.get(self.name, self.name)

    async def chat(
        self, system: str, messages: list[dict], tools: list[dict], *, max_tokens: int
    ) -> LLMTurn:  # pragma: no cover - interface
        raise NotImplementedError

    def user_message(self, text: str) -> list[dict]:
        return [{"role": "user", "content": text}]

    def assistant_messages(self, turn: LLMTurn) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError

    def tool_result_messages(self, results: list[ToolResult]) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError

    async def aclose(self) -> None:
        client = getattr(self, "client", None)
        if self._owns_client and client is not None and hasattr(client, "close"):
            try:
                await client.close()
            except Exception:
                pass

    def _require_live(self) -> None:
        if not self.live or getattr(self, "client", None) is None:
            raise LLMUnavailable("no_key", f"{self.label}: no API key configured")


# ----------------------------------------------------------------------------------------------------------
# Anthropic
# ----------------------------------------------------------------------------------------------------------


def _make_anthropic_client(api_key: str) -> Any:
    import anthropic

    return anthropic.AsyncAnthropic(api_key=api_key)


_ANTHROPIC_END = {"end_turn", "stop_sequence", "pause_turn"}
_ANTHROPIC_MAX = {"max_tokens", "model_context_window_exceeded"}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, model: str, client: Any = None, live: bool = True) -> None:
        super().__init__(model=model, live=live)
        self.client = client

    async def chat(self, system: str, messages: list[dict], tools: list[dict], *, max_tokens: int) -> LLMTurn:
        import anthropic

        self._require_live()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "output_config": {"effort": EFFORT},
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": "auto"}  # forced tool choice returns 400 on these models
        try:
            response = await self.client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable("rate_limit", f"Anthropic API error: {type(exc).__name__}") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailable("auth", f"Anthropic API error: {type(exc).__name__}") from exc
        except anthropic.APIError as exc:
            log.warning("anthropic API error: %s", exc)
            raise LLMUnavailable("error", f"Anthropic API error: {type(exc).__name__}") from exc

        content = list(response.content or [])
        text = "\n".join(
            b.text for b in content if getattr(b, "type", None) == "text" and getattr(b, "text", None)
        ).strip()
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in content
            if getattr(b, "type", None) == "tool_use"
        ]
        stop_reason = response.stop_reason
        detail = ""
        if stop_reason == "refusal":
            stop: Stop = "refusal"
        elif stop_reason in _ANTHROPIC_MAX:
            stop = "max_tokens"
        elif stop_reason == "tool_use" and calls:
            stop = "tool_use"
        else:
            if stop_reason not in _ANTHROPIC_END and stop_reason is not None:
                log.info("unexpected stop_reason %s treated as end of turn", stop_reason)
            stop = "end"
        if stop in ("refusal", "max_tokens"):
            detail = f"stop_reason={stop_reason}"
            details = getattr(response, "stop_details", None)
            if details is not None:
                extra = f"{getattr(details, 'category', None) or ''} {getattr(details, 'explanation', None) or ''}"
                if extra.strip():
                    detail += f" ({extra.strip()})"
        usage = getattr(response, "usage", None)
        return LLMTurn(
            text=text,
            tool_calls=calls,
            stop=stop,
            raw_assistant=response.content,  # appended verbatim (thinking blocks included)
            usage={
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
            detail=detail,
        )

    def assistant_messages(self, turn: LLMTurn) -> list[dict]:
        if turn.raw_assistant is not None:
            return [{"role": "assistant", "content": turn.raw_assistant}]
        blocks: list[dict[str, Any]] = []
        if turn.text:
            blocks.append({"type": "text", "text": turn.text})
        blocks += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in turn.tool_calls
        ]
        return [{"role": "assistant", "content": blocks or [{"type": "text", "text": "(no content)"}]}]

    def tool_result_messages(self, results: list[ToolResult]) -> list[dict]:
        # Every tool_use in a turn gets its tool_result in ONE user message.
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.id, "content": r.content, "is_error": r.is_error}
                    for r in results
                ],
            }
        ]


# ----------------------------------------------------------------------------------------------------------
# OpenAI-compatible (Groq, Gemini, OpenRouter, Ollama, ...)
# ----------------------------------------------------------------------------------------------------------

# Keys Gemini's OpenAI endpoint understands in a function's parameters (its OpenAPI-subset Schema). Everything
# else (additionalProperties, strict, $schema, default, const, oneOf, ...) is dropped or rewritten.
_GEMINI_SCHEMA_KEYS = {
    "type",
    "description",
    "enum",
    "properties",
    "required",
    "items",
    "anyOf",
    "nullable",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "title",
}
_GEMINI_FORMATS = {"enum", "date-time"}


def sanitize_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a JSON schema into the subset Gemini's OpenAI-compatible endpoint accepts.

    Inlines ``$ref``/``$defs``, turns ``{"type": [T, "null"]}`` and ``anyOf [X, null]`` into
    ``{"type": T, "nullable": true}``, maps ``const`` to a one-value ``enum``, and keeps only whitelisted keys.
    """
    defs: dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        if isinstance(schema.get(key), dict):
            defs.update(schema[key])

    def resolve(node: Any, depth: int = 0) -> Any:
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and depth < 20:
            target = defs.get(ref.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, depth + 1)
            return {"type": "string"}
        return node

    def clean(node: Any, depth: int = 0) -> dict[str, Any]:
        node = resolve(node, depth)
        if not isinstance(node, dict):
            return {}
        out: dict[str, Any] = {}
        nullable = bool(node.get("nullable"))
        typ = node.get("type")
        if isinstance(typ, list):
            non_null = [t for t in typ if t != "null"]
            nullable = nullable or len(non_null) != len(typ)
            typ = non_null[0] if non_null else "string"
        variants = node.get("anyOf") or node.get("oneOf")
        if isinstance(variants, list):
            non_null_v = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
            nullable = nullable or len(non_null_v) != len(variants)
            if len(non_null_v) == 1:
                inner = clean(non_null_v[0], depth + 1)
                for k in ("description", "title"):
                    if isinstance(node.get(k), str):
                        inner[k] = node[k]
                if nullable:
                    inner["nullable"] = True
                return inner
            if non_null_v:
                out["anyOf"] = [clean(v, depth + 1) for v in non_null_v]
        if typ is not None:
            out["type"] = typ
        if "const" in node and "enum" not in node:
            out["enum"] = [node["const"]]
        for key, value in node.items():
            if key not in _GEMINI_SCHEMA_KEYS or key in ("type", "anyOf", "nullable") or value is None:
                continue
            if key == "properties" and isinstance(value, dict):
                out["properties"] = {k: clean(v, depth + 1) for k, v in value.items()}
            elif key == "items":
                out["items"] = clean(value, depth + 1)
            elif key == "format":
                if value in _GEMINI_FORMATS:
                    out["format"] = value
            elif key == "required" and isinstance(value, list):
                out["required"] = list(value)
            else:
                out[key] = value
        if "required" in out and isinstance(out.get("properties"), dict):
            out["required"] = [r for r in out["required"] if r in out["properties"]]
        if nullable:
            out["nullable"] = True
        if out.get("type") == "object" and "properties" not in out:
            out["properties"] = {}
        return out

    cleaned = clean(schema)
    cleaned.setdefault("type", "object")
    return cleaned


def to_openai_tool(tool: dict[str, Any], *, provider: str) -> dict[str, Any]:
    """Convert our neutral (Anthropic-shaped) tool dict to an OpenAI ``{"type": "function", ...}`` tool."""
    schema = copy.deepcopy(tool.get("input_schema") or {"type": "object", "properties": {}})
    if provider == "gemini":
        schema = sanitize_schema_for_gemini(schema)
    else:
        schema.pop("$schema", None)
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "parameters": schema,
        },
    }


OPENAI_MAX_RETRIES = 0
OPENAI_TIMEOUT_S = 20.0


def _make_openai_client(*, api_key: str, base_url: str) -> Any:
    import openai

    # No SDK retries: its retry honours Retry-After (up to 60 s) and a free-tier 429 must drop to rules mode
    # at once instead of freezing the demo. 20 s per call bounds a slow endpoint the same way.
    return openai.AsyncOpenAI(
        api_key=api_key, base_url=base_url, max_retries=OPENAI_MAX_RETRIES, timeout=OPENAI_TIMEOUT_S
    )


_TOOL_CALL_CORE_KEYS = {"id", "type", "function", "index"}


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        model: str,
        client: Any = None,
        live: bool = True,
        reasoning_effort: str = "",
    ) -> None:
        super().__init__(model=model, live=live)
        self.name = name
        self.client = client
        self.reasoning_effort = reasoning_effort

    def convert_tools(self, tools: list[dict]) -> list[dict]:
        return [to_openai_tool(t, provider=self.name) for t in tools]

    async def chat(self, system: str, messages: list[dict], tools: list[dict], *, max_tokens: int) -> LLMTurn:
        import openai

        self._require_live()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self.convert_tools(tools)
            kwargs["tool_choice"] = "auto"
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        where = f"{self.label} API error"
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise LLMUnavailable("rate_limit", f"{where}: {type(exc).__name__}") from exc
        except openai.AuthenticationError as exc:
            raise LLMUnavailable("auth", f"{where}: {type(exc).__name__}") from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise LLMUnavailable("unavailable", f"{where}: {type(exc).__name__}") from exc
        except openai.APIStatusError as exc:
            kind = "rate_limit" if exc.status_code == 429 else "error"
            log.warning("%s: %s %s", where, exc.status_code, type(exc).__name__)
            raise LLMUnavailable(kind, f"{where}: {type(exc).__name__} ({exc.status_code})") from exc
        except openai.OpenAIError as exc:
            raise LLMUnavailable("error", f"{where}: {type(exc).__name__}") from exc
        return self.parse_response(response)

    def parse_response(self, response: Any) -> LLMTurn:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return LLMTurn(stop="error", detail="empty response (no choices)")
        choice = choices[0]
        message = choice.message
        finish = getattr(choice, "finish_reason", None)
        text = (getattr(message, "content", None) or "").strip()
        usage_obj = getattr(response, "usage", None)
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", None),
            "output_tokens": getattr(usage_obj, "completion_tokens", None),
        }

        calls: list[ToolCall] = []
        for tc in getattr(message, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) or ""
            raw_args = getattr(fn, "arguments", None)
            try:
                args = json.loads(raw_args) if raw_args not in (None, "") else {}
            except (TypeError, ValueError):
                return LLMTurn(
                    text=text, stop="error", usage=usage, detail=f"invalid JSON arguments for {name}"
                )
            if not isinstance(args, dict):
                return LLMTurn(
                    text=text, stop="error", usage=usage, detail=f"non-object arguments for {name}"
                )
            extra: dict[str, Any] = {}
            if hasattr(tc, "model_dump"):
                dumped = tc.model_dump(exclude_none=True)
                extra = {k: v for k, v in dumped.items() if k not in _TOOL_CALL_CORE_KEYS}
            calls.append(
                ToolCall(
                    id=getattr(tc, "id", None) or f"call_{len(calls)}", name=name, arguments=args, extra=extra
                )
            )

        refusal = getattr(message, "refusal", None)
        if finish == "content_filter" or refusal:
            turn = LLMTurn(
                text=text,
                stop="refusal",
                usage=usage,
                detail=f"finish_reason={finish}" + (f" ({refusal})" if refusal else ""),
            )
        elif finish == "length":
            turn = LLMTurn(text=text, stop="max_tokens", usage=usage, detail="finish_reason=length")
        elif calls:
            # Gemini's endpoint may report finish_reason "stop" together with tool calls.
            turn = LLMTurn(text=text, tool_calls=calls, stop="tool_use", usage=usage)
        else:
            turn = LLMTurn(text=text, stop="end", usage=usage)
        turn.raw_assistant = self._assistant_dict(turn)
        return turn

    def _assistant_dict(self, turn: LLMTurn) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
        if turn.tool_calls:
            msg["tool_calls"] = [
                {
                    **c.extra,
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in turn.tool_calls
            ]
        return msg

    def assistant_messages(self, turn: LLMTurn) -> list[dict]:
        raw = turn.raw_assistant if isinstance(turn.raw_assistant, dict) else self._assistant_dict(turn)
        return [raw]

    def tool_result_messages(self, results: list[ToolResult]) -> list[dict]:
        return [{"role": "tool", "tool_call_id": r.id, "content": r.content} for r in results]


# ----------------------------------------------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------------------------------------------


def _build(provider: str, model: str, settings: Settings, *, live: bool) -> LLMProvider:
    if provider == "anthropic":
        p: LLMProvider = AnthropicProvider(
            model=model, client=_make_anthropic_client(settings.llm_key) if live else None, live=live
        )
    else:
        p = OpenAICompatProvider(
            name=provider,
            model=model,
            client=_make_openai_client(api_key=settings.llm_key, base_url=settings.llm_base_url)
            if live
            else None,
            live=live,
            reasoning_effort=settings.llm_reasoning_effort,
        )
    p._owns_client = live
    return p


def get_llm_provider(settings: Settings | None = None, *, live: bool = True) -> LLMProvider | None:
    """The configured provider, or a replay-only one, or None (rules mode).

    - A key is configured and ``live``: a live provider (``TK_LLM_PROVIDER`` / inferred, see app.config).
    - Otherwise, when the cache may replay (``TK_LLM_CACHE`` replay|auto) and AI is not switched off
      (``TK_LLM_PROVIDER=none``): a replay-only provider for ``settings.llm_replay_identity``.
    - Otherwise None.
    """
    settings = settings or get_settings()
    if live and settings.llm_configured:
        return _build(settings.llm_provider, settings.llm_model, settings, live=True)
    identity = replay_identity(settings)
    if identity and settings.llm_cache in ("replay", "auto"):
        return _build(identity[0], identity[1], settings, live=False)
    return None


# ----------------------------------------------------------------------------------------------------------
# Replay cache (table ``llm_cache``: key = sha256 hex, json = the recorded turn, circle_id unused/None)
# ----------------------------------------------------------------------------------------------------------

CIRCLE_PLACEHOLDER = "{circle}"
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
# Values that differ between otherwise identical runs (PayPal/run IDs, timestamps, row ids).
VOLATILE_KEYS = frozenset(
    {
        "id", "order_id", "capture_id", "batch_id", "item_id", "payout_batch_id", "payout_item_id",
        "request_id", "vault_id", "customer_id", "debug_id", "run_id", "sid", "ts", "created_at",
        "updated_at", "last_capture_at", "last_attempt_at", "paid_at", "virtual_now", "links", "ms",
        "receiver", "payout_receiver", "sandbox_account",
        # Recovery ids are random per session (the model works with the logical "rs:<member>:r<n>" handle).
        "set_id", "remedy_set_id", "remedy_ids", "remedy_id", "invoice_id",
    }
)  # fmt: skip
# Raw remedy set / remedy ids inside prose ("Proposal rs_ab12cd34 ...").
_RECOVERY_ID_RE = re.compile(r"(rs|rm)_[0-9a-f]{8}")


def _norm_str(s: str, circle_id: str) -> str:
    stripped = s.strip()
    if stripped[:1] in ("{", "[") and len(stripped) > 1:
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict | list):
            return json.dumps(normalize(parsed, circle_id), sort_keys=True)
    if circle_id:
        s = s.replace(circle_id, CIRCLE_PLACEHOLDER)
    s = _RECOVERY_ID_RE.sub(r"_{id}", s)
    return _ISO_TS_RE.sub("{ts}", s)


def normalize(obj: Any, circle_id: str) -> Any:
    """Normalize a request fragment for the cache key: logical handles only, no run-specific values."""
    if isinstance(obj, str):
        return _norm_str(obj, circle_id)
    if isinstance(obj, dict):
        return {
            k: ("{v}" if k in VOLATILE_KEYS and v not in (None, "", [], {}) else normalize(v, circle_id))
            for k, v in obj.items()
        }
    if isinstance(obj, list | tuple):
        return [normalize(v, circle_id) for v in obj]
    return obj


def _swap_circle(obj: Any, old: str, new: str) -> Any:
    if not old:
        return obj
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, dict):
        return {k: _swap_circle(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_swap_circle(v, old, new) for v in obj]
    return obj


def neutral_key_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace provider-generated tool-call ids by their position so the key is stable across providers' ids."""
    ids: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for msg in history:
        msg = copy.deepcopy(msg)
        for call in msg.get("tool_calls", []) or []:
            call["id"] = ids.setdefault(call.get("id", ""), f"call_{len(ids)}")
        for res in msg.get("results", []) or []:
            res["id"] = ids.setdefault(res.get("id", ""), f"call_{len(ids)}")
        out.append(msg)
    return out


def cache_key(
    *, provider: str, model: str, system: str, tools: list[dict], history: list[dict], circle_id: str = ""
) -> str:
    """sha256 over provider + model + system + tools + the normalized, provider-neutral history."""
    payload = {
        "provider": provider,
        "model": model,
        "system": normalize(system, circle_id),
        "tools": normalize(tools, circle_id),
        "messages": normalize(neutral_key_history(history), circle_id),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Values under these keys are not worth scrubbing from prose (timestamps are fine to show; durations are ints).
_SCRUB_SKIP_KEYS = frozenset({"ts", "created_at", "updated_at", "last_capture_at", "last_attempt_at", "paid_at",
                              "virtual_now", "ms", "links"})  # fmt: skip
SCRUB_MIN_LEN = 6  # shorter values (member handles, "ok") are never treated as ids
SCRUB_TEXT_ARG_TOOLS = frozenset({"record_note"})  # the only free-text tool argument


def collect_volatile_values(obj: Any, out: dict[str, str], *, keep: str = "") -> dict[str, str]:
    """Remember every run-specific string found under ``VOLATILE_KEYS`` in a tool result -> ``"{key}"``.

    ``obj`` may be a JSON string, dict or list. ``keep`` (the circle id) is never collected: it already has
    its own ``{circle}`` placeholder.
    """
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped[:1] in ("{", "[") and len(stripped) > 1:
            try:
                collect_volatile_values(json.loads(stripped), out, keep=keep)
            except ValueError:
                pass
        return out

    def _add(value: Any, key: str) -> None:
        if isinstance(value, str) and len(value) >= SCRUB_MIN_LEN and value != keep:
            out.setdefault(value, "{" + key + "}")

    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in VOLATILE_KEYS and k not in _SCRUB_SKIP_KEYS:
                if isinstance(v, list):
                    for item in v:
                        _add(item, k)
                elif isinstance(v, str):
                    _add(v, k)
                else:
                    collect_volatile_values(v, out, keep=keep)
            else:
                collect_volatile_values(v, out, keep=keep)
    elif isinstance(obj, list | tuple):
        for v in obj:
            collect_volatile_values(v, out, keep=keep)
    return out


def scrub_text(text: str, mapping: dict[str, str]) -> str:
    """Replace each collected volatile value in ``text`` by its placeholder (longest values first)."""
    if not text or not mapping:
        return text
    for value in sorted(mapping, key=len, reverse=True):
        if value in text:
            text = text.replace(value, mapping[value])
    return text


class TurnCache:
    """Reads/writes recorded turns. Every DB failure degrades to a miss / no-op (the demo must not break)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    @property
    def can_replay(self) -> bool:
        return self.mode in ("replay", "auto")

    @property
    def can_record(self) -> bool:
        return self.mode in ("record", "auto")

    def get(self, key: str, *, circle_id: str = "") -> LLMTurn | None:
        if not self.can_replay:
            return None
        try:
            from sqlmodel import select

            from app.db import session_scope
            from app.models import LlmCache

            with session_scope() as db:
                row = db.exec(
                    select(LlmCache).where(LlmCache.key == key).order_by(LlmCache.id.desc())
                ).first()
                data = json.loads(row.json) if row is not None else None
        except Exception as exc:
            log.info("llm cache read failed: %s", exc)
            return None
        if not data or "turn" not in data:
            return None
        turn = LLMTurn.model_validate(_swap_circle(data["turn"], CIRCLE_PLACEHOLDER, circle_id))
        turn.raw_assistant = None
        return turn

    def put(
        self,
        key: str,
        turn: LLMTurn,
        *,
        provider: str,
        model: str,
        circle_id: str = "",
        scrub: dict[str, str] | None = None,
    ) -> None:
        """Record a turn. ``scrub`` maps this run's volatile values (PayPal ids, run id, receivers; see
        ``collect_volatile_values``) to placeholders such as ``{batch_id}``: they are replaced in the stored
        text and in record_note's text, so a replay in another session never shows this session's ids."""
        if not self.can_record or turn.stop not in ("tool_use", "end"):
            return
        stored = turn.model_dump(include={"text", "tool_calls", "stop", "usage"})
        if scrub:
            stored["text"] = scrub_text(stored.get("text") or "", scrub)
            for call in stored.get("tool_calls") or []:
                args = call.get("arguments") or {}
                if call.get("name") in SCRUB_TEXT_ARG_TOOLS and isinstance(args.get("text"), str):
                    args["text"] = scrub_text(args["text"], scrub)
        data = {
            "provider": provider,
            "model": model,
            "turn": _swap_circle(stored, circle_id, CIRCLE_PLACEHOLDER),
        }
        try:
            from sqlmodel import select

            from app.db import session_scope
            from app.models import LlmCache

            with session_scope() as db:
                row = db.exec(select(LlmCache).where(LlmCache.key == key)).first()
                if row is None:
                    row = LlmCache(key=key)
                row.json = json.dumps(data, sort_keys=True, default=str)
                row.circle_id = None
                db.add(row)
        except Exception as exc:
            log.info("llm cache write failed: %s", exc)


# ----------------------------------------------------------------------------------------------------------
# Shipped recordings (app/fixtures/llm_cache.jsonl): record once with a free key, replay on any deploy
# ----------------------------------------------------------------------------------------------------------

SHIPPED_CACHE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "llm_cache.jsonl"
_shipped_memo: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def read_shipped_cache(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Rows of the shipped recording file: ``{"key", "provider", "model", "json"}`` (bad lines skipped)."""
    p = Path(path) if path is not None else SHIPPED_CACHE_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    memo = _shipped_memo.get(str(p))
    if memo is not None and memo[0] == mtime:
        return memo[1]
    rows: list[dict[str, Any]] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.warning("cannot read shipped llm cache %s: %s", p, exc)
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("key") and isinstance(row.get("json"), str):
            rows.append(row)
    _shipped_memo[str(p)] = (mtime, rows)
    return rows


def shipped_identity(path: Path | str | None = None) -> tuple[str, str] | None:
    """The (provider, model) most of the shipped recordings were made with, or None when nothing is shipped."""
    counts: dict[tuple[str, str], int] = {}
    for row in read_shipped_cache(path):
        ident = (str(row.get("provider") or ""), str(row.get("model") or ""))
        if all(ident):
            counts[ident] = counts.get(ident, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def load_shipped_cache(path: Path | str | None = None) -> int:
    """Insert shipped recordings whose key is not in ``llm_cache`` yet (startup). Returns rows inserted."""
    rows = read_shipped_cache(path)
    if not rows:
        return 0
    from sqlmodel import select

    from app.db import session_scope
    from app.models import LlmCache

    inserted = 0
    try:
        with session_scope() as db:
            have = set(db.exec(select(LlmCache.key)).all())
            for row in rows:
                if row["key"] in have:
                    continue
                db.add(LlmCache(key=row["key"], json=row["json"], circle_id=None))
                have.add(row["key"])
                inserted += 1
    except Exception as exc:  # the demo must boot without its recordings
        log.warning("loading shipped llm cache failed: %s", exc)
        return 0
    return inserted


def replay_identity(settings: Settings) -> tuple[str, str] | None:
    """Whose recorded turns a keyless run replays: the settings' identity, except that with no key and no
    explicit TK_LLM_PROVIDER / TK_LLM_MODEL the shipped recordings' own (provider, model) wins, so a run
    recorded with Gemini replays on a deploy whose default would be Groq."""
    identity = settings.llm_replay_identity
    if identity is None or settings.llm_configured:
        return identity
    if settings.llm_provider_setting or settings.llm_model_setting:
        return identity
    return shipped_identity() or identity


# ----------------------------------------------------------------------------------------------------------
# Live-call limiter (count only; routes enforce)
# ----------------------------------------------------------------------------------------------------------

_WINDOW_S = 3600.0
_live_calls: dict[str, deque[float]] = {}
_live_lock = threading.Lock()


def allow_live_call(client_ip: str, *, now: float | None = None, limit: int | None = None) -> bool:
    """Sliding one-hour window per IP. True (and counted) when this IP may start another live run.

    ``limit`` defaults to ``settings.live_llm_per_ip_hour``; a negative limit means unlimited, 0 means never.
    """
    limit = get_settings().live_llm_per_ip_hour if limit is None else limit
    if limit < 0:
        return True
    now = time.monotonic() if now is None else now
    ip = client_ip or "unknown"
    with _live_lock:
        window = _live_calls.setdefault(ip, deque())
        while window and now - window[0] >= _WINDOW_S:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        if len(_live_calls) > 10_000:  # bound memory: drop idle IPs
            for k in [k for k, v in _live_calls.items() if not v or now - v[-1] >= _WINDOW_S]:
                _live_calls.pop(k, None)
        return True


def reset_live_calls() -> None:
    with _live_lock:
        _live_calls.clear()


# ----------------------------------------------------------------------------------------------------------
# One-shot text helper for other features (charter compiler, remedy wording, forecast explanations)
# ----------------------------------------------------------------------------------------------------------


async def complete_text(
    system: str,
    prompt: str,
    *,
    max_tokens: int = 1024,
    provider: LLMProvider | None = None,
    live_allowed: bool = True,
) -> tuple[str | None, str]:
    """One tool-free turn through the provider layer and the replay cache.

    Returns ``(text, mode)`` with mode "live" | "replay" | "rules"; ``text`` is None when no model answered
    (callers then use their deterministic fallback and label it honestly).
    """
    settings = get_settings()
    owns = provider is None
    provider = provider or get_llm_provider(settings, live=live_allowed)
    if provider is None:
        return None, "rules"
    cache = TurnCache(settings.llm_cache)
    history = [{"role": "user", "text": prompt}]
    key = cache_key(provider=provider.name, model=provider.model, system=system, tools=[], history=history)
    try:
        cached = cache.get(key)
        if cached is not None and cached.stop == "end":
            return cached.text, "replay"
        if not provider.live or cache.mode == "replay":
            return None, "rules"
        try:
            turn = await provider.chat(system, provider.user_message(prompt), [], max_tokens=max_tokens)
        except LLMUnavailable as exc:
            log.info("complete_text unavailable: %s", exc.detail)
            return None, "rules"
        if turn.stop != "end" or not turn.text:
            return None, "rules"
        cache.put(key, turn, provider=provider.name, model=provider.model)
        return turn.text, "live"
    finally:
        if owns:
            await provider.aclose()
