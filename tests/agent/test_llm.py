"""Provider-agnostic LLM layer: factory, OpenAI-compat conversion, Gemini sanitizer, replay cache, fallbacks,
live-call limiter. Fake clients only; nothing here touches the network."""

import json

import httpx
import openai
import pytest
from engine_fakes import DEMO_CIRCLE
from openai.types.chat import ChatCompletion
from pydantic import ValidationError

from app.agent import llm, treasurer
from app.agent.llm import (
    AnthropicProvider,
    LLMProvider,
    LLMTurn,
    LLMUnavailable,
    OpenAICompatProvider,
    ToolCall,
    ToolResult,
    allow_live_call,
    cache_key,
    get_llm_provider,
    sanitize_schema_for_gemini,
    to_openai_tool,
)
from app.agent.treasurer import AgentTask, run_task
from app.config import Settings, get_settings
from app.mcp.internal import paypal_ops_internal
from app.mcp.strict_schema import to_anthropic_tool

# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def _env(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return get_settings()


def completion(content=None, tool_calls=None, finish="stop", refusal=None) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "cmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                        "refusal": refusal,
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )


def tc(id_, name, args, **extra) -> dict:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}, **extra}


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": json.loads(json.dumps(kwargs["messages"]))})
        if not self.script:
            raise AssertionError("unexpected live model call")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class FakeOpenAI:
    def __init__(self, script):
        self.completions = FakeCompletions(script)
        self.chat = type("Chat", (), {"completions": self.completions})()
        self.closed = False

    async def close(self):
        self.closed = True


def groq(script, **kw) -> OpenAICompatProvider:
    return OpenAICompatProvider(name="groq", model="openai/gpt-oss-120b", client=FakeOpenAI(script), **kw)


def _task(kind="collect_round") -> AgentTask:
    return AgentTask(kind=kind, circle_id=DEMO_CIRCLE, round_n=3, sid="sid-1", context={"source": "test"})


def _http_error(cls, status: int):
    request = httpx.Request("POST", "https://llm.example.invalid/v1/chat/completions")
    return cls("simulated", response=httpx.Response(status, request=request), body=None)


SCRIPT_OPENAI = [
    completion(
        "Reading the board first.", [tc("call_a", "get_circle", {"circle_id": DEMO_CIRCLE})], "tool_calls"
    ),
    completion(
        None,
        [
            tc("call_b", "collect_round", {"circle_id": DEMO_CIRCLE, "round_n": 3}),
            tc("call_c", "record_note", {"circle_id": DEMO_CIRCLE, "text": "Collecting round 3 now."}),
        ],
        "tool_calls",
    ),
    completion("Round 3 collected: six captures.", None, "stop"),
]


@pytest.fixture
def clean_cache():
    from sqlmodel import delete

    from app.db import session_scope
    from app.models import LlmCache

    def wipe():
        with session_scope() as db:
            db.exec(delete(LlmCache))

    wipe()
    yield
    wipe()


async def _mcp_tools() -> list[dict]:
    import fastmcp

    async with fastmcp.Client(paypal_ops_internal) as mcp:
        return [to_anthropic_tool(t) for t in await mcp.list_tools()]


# ---------------------------------------------------------------------------------------------------------
# settings + factory
# ---------------------------------------------------------------------------------------------------------


def test_no_key_and_cache_off_means_rules_mode(monkeypatch):
    s = _env(monkeypatch, TK_LLM_CACHE="off")
    assert s.llm_provider == "none" and s.llm_configured is False
    assert get_llm_provider(s) is None


def test_no_key_with_auto_cache_gives_a_replay_only_free_provider(monkeypatch):
    s = _env(monkeypatch, TK_LLM_CACHE="auto")
    p = get_llm_provider(s)
    assert isinstance(p, OpenAICompatProvider) and p.live is False and p.client is None
    assert (p.name, p.model) == ("groq", "openai/gpt-oss-120b")
    # AI switched off explicitly: not even replay.
    s = _env(monkeypatch, TK_LLM_PROVIDER="none", TK_LLM_API_KEY="gsk_test_not_real")
    assert s.llm_configured is False and get_llm_provider(s) is None


@pytest.mark.parametrize(
    "env, provider, model, base_url",
    [
        (
            {"TK_LLM_API_KEY": "gsk_test_not_real"},
            "groq",
            "openai/gpt-oss-120b",
            "https://api.groq.com/openai/v1",
        ),
        (
            {"TK_LLM_API_KEY": "AIza-test-not-real"},
            "gemini",
            "gemini-3.8-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        (
            {
                "TK_LLM_PROVIDER": "gemini",
                "TK_LLM_API_KEY": "test-not-real",
                "TK_LLM_MODEL": "gemini-3.5-flash-lite",
            },
            "gemini",
            "gemini-3.5-flash-lite",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        (
            {
                "TK_LLM_API_KEY": "test-not-real",
                "TK_LLM_BASE_URL": "http://localhost:11434/v1",
                "TK_LLM_MODEL": "qwen3",
            },
            "openai_compat",
            "qwen3",
            "http://localhost:11434/v1",
        ),
    ],
)
async def test_factory_builds_openai_compat_providers(monkeypatch, env, provider, model, base_url):
    s = _env(monkeypatch, **env)
    assert s.llm_configured and s.llm_provider == provider and s.llm_model == model
    p = get_llm_provider(s)
    assert isinstance(p, OpenAICompatProvider) and p.live is True
    assert (p.name, p.model) == (provider, model)
    assert isinstance(p.client, openai.AsyncOpenAI)
    assert str(p.client.base_url).rstrip("/") == base_url.rstrip("/")
    assert p.client.max_retries == 0 and p.client.timeout == 20.0  # a free-tier 429 falls back at once
    await p.aclose()


async def test_factory_builds_anthropic_from_the_m1_settings(monkeypatch):
    s = _env(monkeypatch, ANTHROPIC_API_KEY="test-key-not-real")
    assert s.llm_provider == "anthropic" and s.llm_model == "claude-opus-5-5" and s.llm_max_tokens == 8000
    p = get_llm_provider(s)
    assert isinstance(p, AnthropicProvider) and p.client.api_key == "test-key-not-real"
    await p.aclose()
    # The free key wins when both are set without an explicit provider; an explicit switch picks Anthropic.
    s = _env(monkeypatch, TK_LLM_API_KEY="gsk_test_not_real")
    assert s.llm_provider == "groq" and s.llm_max_tokens == 4096
    s = _env(monkeypatch, TK_LLM_PROVIDER="anthropic", TK_LLM_MODEL="claude-haiku-5-5")
    assert s.llm_key == "test-key-not-real" and s.llm_model == "claude-haiku-5-5"


def test_openai_compat_needs_a_base_url_and_bad_values_are_rejected(monkeypatch):
    s = _env(monkeypatch, TK_LLM_PROVIDER="openai_compat", TK_LLM_API_KEY="k", TK_LLM_MODEL="m")
    assert s.llm_configured is False
    monkeypatch.setenv("TK_LLM_PROVIDER", "github")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("TK_LLM_PROVIDER", "")
    monkeypatch.setenv("TK_LLM_CACHE", "sometimes")
    with pytest.raises(ValidationError):
        Settings()


# ---------------------------------------------------------------------------------------------------------
# OpenAI-compat conversion
# ---------------------------------------------------------------------------------------------------------


async def test_tool_schemas_convert_to_openai_functions(fake_engine):
    tools = await _mcp_tools()
    out = [to_openai_tool(t, provider="groq") for t in tools]
    from app.mcp.internal import TOOL_NAMES  # M1 tools + the B6 recovery tools

    assert len(out) == len(tools) == len(TOOL_NAMES)
    for neutral, fn in zip(tools, out, strict=True):
        assert fn["type"] == "function" and set(fn) == {"type", "function"}
        assert fn["function"]["name"] == neutral["name"]
        assert fn["function"]["description"] == neutral["description"]
        assert fn["function"]["parameters"] == neutral["input_schema"]
        assert "strict" not in fn["function"]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


async def test_gemini_sanitizer_over_every_registered_tool(fake_engine):
    for tool in await _mcp_tools():
        params = to_openai_tool(tool, provider="gemini")["function"]["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) <= set(params["properties"])
        for node in _walk(params):
            assert "additionalProperties" not in node and "strict" not in node and "default" not in node
            assert not isinstance(node.get("type"), list)


def test_gemini_sanitizer_rewrites_unsupported_keywords():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "member": {"type": ["string", "null"], "description": "handle", "default": None},
            "kind": {"const": "retry"},
            "when": {"type": "string", "format": "uuid"},
            "at": {"type": "string", "format": "date-time"},
            "plan": {"$ref": "#/$defs/Plan"},
            "maybe": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}], "description": "round"},
        },
        "required": ["member", "kind", "when", "at", "plan", "maybe", "ghost"],
        "$defs": {
            "Plan": {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "additionalProperties": False,
            }
        },
    }
    out = sanitize_schema_for_gemini(schema)
    props = out["properties"]
    assert "$schema" not in out and "$defs" not in out and "additionalProperties" not in out
    assert props["member"] == {"type": "string", "description": "handle", "nullable": True}
    assert props["kind"] == {"enum": ["retry"]}
    assert props["when"] == {"type": "string"}
    assert props["at"] == {"type": "string", "format": "date-time"}
    assert props["plan"] == {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert props["maybe"] == {"type": "integer", "minimum": 1, "nullable": True, "description": "round"}
    assert out["required"] == ["member", "kind", "when", "at", "plan", "maybe"]  # unknown names dropped


def test_tool_calls_parse_back_and_extras_survive():
    p = groq([])
    turn = p.parse_response(
        completion(
            "checking",
            [
                tc(
                    "call_1",
                    "get_circle",
                    {"circle_id": DEMO_CIRCLE},
                    extra_content={"google": {"thought_signature": "sig"}},
                )
            ],
            "stop",  # Gemini reports "stop" alongside tool calls
        )
    )
    assert turn.stop == "tool_use" and turn.text == "checking"
    assert turn.tool_calls == [
        ToolCall(
            id="call_1",
            name="get_circle",
            arguments={"circle_id": DEMO_CIRCLE},
            extra={"extra_content": {"google": {"thought_signature": "sig"}}},
        )
    ]
    assert turn.usage == {"input_tokens": 10, "output_tokens": 5}
    [assistant] = p.assistant_messages(turn)
    assert assistant["role"] == "assistant" and assistant["content"] == "checking"
    call = assistant["tool_calls"][0]
    assert call["id"] == "call_1" and call["type"] == "function"
    assert json.loads(call["function"]["arguments"]) == {"circle_id": DEMO_CIRCLE}
    assert call["extra_content"] == {"google": {"thought_signature": "sig"}}
    assert p.tool_result_messages([ToolResult("call_1", "get_circle", '{"ok": true}')]) == [
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'}
    ]
    empty_args = p.parse_response(completion(None, [tc("call_2", "get_circle", "")], "tool_calls"))
    assert empty_args.tool_calls[0].arguments == {}


@pytest.mark.parametrize(
    "response, stop, detail",
    [
        (completion(None, [tc("c", "get_circle", "{not json")], "tool_calls"), "error", "invalid JSON"),
        (completion(None, [tc("c", "get_circle", "[1, 2]")], "tool_calls"), "error", "non-object"),
        (completion("partial", None, "length"), "max_tokens", "finish_reason=length"),
        (completion(None, None, "content_filter"), "refusal", "finish_reason=content_filter"),
        (completion(None, None, "stop", refusal="cannot help"), "refusal", "cannot help"),
        (completion("all done", None, "stop"), "end", ""),
    ],
)
def test_finish_reasons_and_bad_arguments_map_to_stops(response, stop, detail):
    turn = groq([]).parse_response(response)
    assert turn.stop == stop and detail in turn.detail


def test_no_choices_is_an_error_turn():
    response = completion("x")
    response.choices = []
    assert groq([]).parse_response(response).stop == "error"


async def test_chat_sends_openai_shaped_request_and_maps_errors(fake_engine):
    tools = await _mcp_tools()
    p = OpenAICompatProvider(
        name="gemini", model="gemini-3.8-flash", client=FakeOpenAI([completion("hi")]), reasoning_effort="low"
    )
    turn = await p.chat("SYSTEM", p.user_message("hello"), tools, max_tokens=123)
    assert turn.stop == "end" and turn.text == "hi"
    sent = p.client.completions.calls[0]
    assert sent["model"] == "gemini-3.8-flash" and sent["max_tokens"] == 123 and sent["tool_choice"] == "auto"
    assert sent["messages"] == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "hello"}]
    assert sent["reasoning_effort"] == "low"
    assert all("additionalProperties" not in json.dumps(t) for t in sent["tools"])
    for forbidden in ("temperature", "top_p", "stream"):
        assert forbidden not in sent

    cases = [
        (_http_error(openai.RateLimitError, 429), "rate_limit"),
        (_http_error(openai.AuthenticationError, 401), "auth"),
        (_http_error(openai.InternalServerError, 503), "error"),
        (
            openai.APIConnectionError(request=httpx.Request("POST", "https://llm.example.invalid")),
            "unavailable",
        ),
    ]
    for exc, kind in cases:
        p = groq([exc])
        with pytest.raises(LLMUnavailable) as info:
            await p.chat("S", p.user_message("x"), [], max_tokens=10)
        assert info.value.kind == kind and "Groq API error" in info.value.detail


async def test_replay_only_provider_never_calls_the_network():
    p = OpenAICompatProvider(name="groq", model="m", client=None, live=False)
    with pytest.raises(LLMUnavailable) as info:
        await p.chat("s", [], [], max_tokens=10)
    assert info.value.kind == "no_key"


# ---------------------------------------------------------------------------------------------------------
# Treasurer loop through the OpenAI-compat provider (in-memory MCP client, fake engine)
# ---------------------------------------------------------------------------------------------------------


async def test_treasurer_runs_end_to_end_on_a_free_provider(fake_engine, collector):
    provider = groq(list(SCRIPT_OPENAI))
    result = await run_task(_task(), publish=collector, provider=provider)

    assert result.mode == "live" and result.ok is True and result.tool_calls == 3
    assert result.summary == "Round 3 collected: six captures."
    assert fake_engine.calls_named("collect_round") == [{"circle_id": DEMO_CIRCLE, "round_n": 3}]
    assert provider.client.closed is False  # injected providers are not closed

    calls = provider.client.completions.calls
    assert len(calls) == 3
    first = calls[0]
    assert first["model"] == "openai/gpt-oss-120b" and first["max_tokens"] == 4096
    assert first["messages"][0]["role"] == "system"
    assert "You propose. The policy engine decides. Humans approve." in first["messages"][0]["content"]
    assert first["messages"][1] == {"role": "user", "content": treasurer.task_prompt(_task())}
    allowed = treasurer.allowed_tools(_task().kind)  # the task's allowlist, never every tool
    names = {t["function"]["name"] for t in first["tools"]}
    assert names == set(allowed) and all(t["type"] == "function" for t in first["tools"])

    third = calls[2]["messages"][1:]  # drop the system message
    assert [m["role"] for m in third] == ["user", "assistant", "tool", "assistant", "tool", "tool"]
    assert third[1]["tool_calls"][0]["function"]["name"] == "get_circle"
    assert third[2]["tool_call_id"] == "call_a" and json.loads(third[2]["content"])["ok"] is True
    assert [m["tool_call_id"] for m in third[4:]] == ["call_b", "call_c"]
    assert json.loads(third[5]["content"]) == {"ok": True, "chars": 23}

    kinds = collector.kinds()
    assert kinds[:2] == ["system", "text"] and kinds.count("tool_call") == 3
    assert "Live mode: Groq openai/gpt-oss-120b" in collector.events[0]["text"]
    assert all(e["mode"] == "live" for e in result.trace)


async def test_rate_limit_falls_back_to_rules_with_an_honest_trace(fake_engine, collector):
    provider = groq([_http_error(openai.RateLimitError, 429)])
    result = await run_task(_task(), publish=collector, provider=provider)
    assert result.mode == "rules" and result.ok is True
    system = [e["text"] for e in collector.events if e.get("kind") == "system"]
    assert any(t.startswith("AI unavailable (rate limit) - rules mode") for t in system)
    assert any("Rules mode (AI unavailable (rate limit))" in t for t in system)
    assert result.trace[0]["mode"] == "live" and result.trace[-1]["mode"] == "rules"
    assert fake_engine.calls_named("pay_round") == [{"circle_id": DEMO_CIRCLE, "round_n": 3}]


async def test_malformed_tool_arguments_fall_back_to_rules(fake_engine, collector):
    provider = groq([completion(None, [tc("c1", "collect_round", "{oops")], "tool_calls")])
    result = await run_task(_task(), publish=collector, provider=provider)
    assert result.mode == "rules"
    assert any(
        "AI unavailable (malformed model output) - rules mode" in e["text"]
        for e in collector.events
        if e.get("kind") == "system"
    )
    assert fake_engine.calls_named("collect_round") == [
        {"circle_id": DEMO_CIRCLE, "round_n": 3}
    ]  # rules only


async def test_factory_path_builds_and_closes_the_provider(monkeypatch, fake_engine, collector):
    _env(monkeypatch, TK_LLM_API_KEY="gsk_test_not_real")
    built = {}

    def fake_make(*, api_key, base_url):
        built.update(api_key=api_key, base_url=base_url)
        built["client"] = FakeOpenAI([completion("done")])
        return built["client"]

    monkeypatch.setattr(llm, "_make_openai_client", fake_make)
    result = await run_task(_task("payout"), publish=collector)
    assert result.mode == "live" and result.summary == "done"
    assert built["api_key"] == "gsk_test_not_real" and built["base_url"] == "https://api.groq.com/openai/v1"
    assert built["client"].closed is True


# ---------------------------------------------------------------------------------------------------------
# Replay cache
# ---------------------------------------------------------------------------------------------------------


def test_cache_key_normalizes_circle_ids_run_ids_and_timestamps():
    def history(circle, order, ts, call_id):
        result = {"ok": True, "circle_id": circle, "order_id": order, "ts": ts, "status": "COMPLETED"}
        return [
            {"role": "user", "text": f"circle_id: {circle}"},
            {
                "role": "assistant",
                "text": "",
                "tool_calls": [{"id": call_id, "name": "x", "arguments": {"circle_id": circle}}],
            },
            {
                "role": "tool",
                "results": [{"id": call_id, "name": "x", "content": json.dumps(result), "is_error": False}],
            },
        ]

    base = dict(provider="groq", model="m", system="sys", tools=[])
    a = cache_key(
        **base, history=history("abc123", "ORD-1", "2026-10-23T10:00:00Z", "call_q"), circle_id="abc123"
    )
    b = cache_key(
        **base, history=history("xyz789", "ORD-2", "2026-10-24T11:30:00+00:00", "toolu_9"), circle_id="xyz789"
    )
    assert a == b
    assert a != cache_key(
        **{**base, "model": "other"}, history=history("abc123", "ORD-1", "t", "c"), circle_id="abc123"
    )
    assert a != cache_key(
        **{**base, "provider": "gemini"}, history=history("abc123", "ORD-1", "t", "c"), circle_id="abc123"
    )
    changed = history("abc123", "ORD-1", "2026-10-23T10:00:00Z", "call_q")
    changed[2]["results"][0]["content"] = json.dumps({"ok": True, "status": "DECLINED"})
    assert a != cache_key(**base, history=changed, circle_id="abc123")


async def test_record_then_replay_without_any_provider_call(
    monkeypatch, clean_cache, fake_engine_factory, collector
):
    _env(monkeypatch, TK_LLM_CACHE="auto")
    fake_engine_factory()
    live = groq(list(SCRIPT_OPENAI))
    first = await run_task(_task(), publish=collector, provider=live)
    assert first.mode == "live" and len(live.client.completions.calls) == 3

    # Second run: fresh engine, NO key configured at all -> replay-only provider from the factory.
    engine = fake_engine_factory()
    replay_events = type(collector)()
    second = await run_task(_task(), publish=replay_events)
    assert second.mode == "replay" and second.ok is True and second.tool_calls == 3
    assert second.summary == first.summary
    assert engine.calls_named("collect_round") == [
        {"circle_id": DEMO_CIRCLE, "round_n": 3}
    ]  # tools still run
    assert all(e["mode"] == "replay" for e in second.trace)
    assert replay_events.events[0]["text"].startswith("Replay mode: recorded Groq openai/gpt-oss-120b")

    # A live provider in auto mode also replays instead of calling out.
    fake_engine_factory()
    strict = groq([])  # any call raises AssertionError
    third = await run_task(_task(), publish=type(collector)(), provider=strict)
    assert third.mode == "replay" and strict.client.completions.calls == []


async def test_replay_mode_miss_without_key_is_a_plain_rules_run(
    monkeypatch, clean_cache, fake_engine, collector
):
    _env(monkeypatch, TK_LLM_CACHE="replay")
    result = await run_task(_task(), publish=collector)
    assert result.mode == "rules"
    assert "no LLM key" in collector.events[0]["text"] and collector.events[0]["mode"] == "rules"


async def test_replay_mode_never_calls_a_configured_provider(
    monkeypatch, clean_cache, fake_engine, collector
):
    _env(monkeypatch, TK_LLM_CACHE="replay")
    provider = groq([])
    result = await run_task(_task(), publish=collector, provider=provider)
    assert result.mode == "rules" and provider.client.completions.calls == []


async def test_record_mode_overwrites_and_off_mode_never_touches_the_cache(
    monkeypatch, clean_cache, fake_engine_factory, collector
):
    from sqlmodel import select

    from app.db import session_scope
    from app.models import LlmCache

    def rows():
        with session_scope() as db:
            return len(db.exec(select(LlmCache)).all())

    _env(monkeypatch, TK_LLM_CACHE="off")
    fake_engine_factory()
    await run_task(_task(), publish=collector, provider=groq(list(SCRIPT_OPENAI)))
    assert rows() == 0

    _env(monkeypatch, TK_LLM_CACHE="record")
    for _ in range(2):
        fake_engine_factory()
        p = groq(list(SCRIPT_OPENAI))
        result = await run_task(_task(), publish=collector, provider=p)
        assert result.mode == "live" and len(p.client.completions.calls) == 3  # record always calls live
    assert rows() == 3  # one row per distinct request, upserted


async def test_live_limit_reached_replays_or_falls_back(
    monkeypatch, clean_cache, fake_engine_factory, collector
):
    _env(monkeypatch, TK_LLM_API_KEY="gsk_test_not_real", TK_LLM_CACHE="auto")
    monkeypatch.setattr(llm, "_make_openai_client", lambda **kw: FakeOpenAI([]))
    fake_engine_factory()
    result = await run_task(_task(), publish=collector, live_allowed=False)
    assert result.mode == "rules" and "live AI limit reached" in collector.events[0]["text"]

    fake_engine_factory()
    await run_task(_task(), publish=type(collector)(), provider=groq(list(SCRIPT_OPENAI)))  # record
    fake_engine_factory()
    result = await run_task(_task(), publish=type(collector)(), live_allowed=False)
    assert result.mode == "replay"


async def test_complete_text_records_then_replays(monkeypatch, clean_cache):
    _env(monkeypatch, TK_LLM_CACHE="auto")
    p = groq([completion("Ravi pays on the 5th.")])
    assert await llm.complete_text("sys", "when does ravi pay?", provider=p) == (
        "Ravi pays on the 5th.",
        "live",
    )
    assert p.client.completions.calls[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert "tools" not in p.client.completions.calls[0]
    assert await llm.complete_text("sys", "when does ravi pay?") == ("Ravi pays on the 5th.", "replay")
    assert await llm.complete_text("sys", "something else") == (None, "rules")
    p = groq([_http_error(openai.RateLimitError, 429)])
    assert await llm.complete_text("sys", "new question", provider=p) == (None, "rules")


# ---------------------------------------------------------------------------------------------------------
# Live-call limiter
# ---------------------------------------------------------------------------------------------------------


def test_allow_live_call_sliding_window():
    assert [allow_live_call("1.2.3.4", now=0.0, limit=2) for _ in range(3)] == [True, True, False]
    assert allow_live_call("5.6.7.8", now=1.0, limit=2) is True  # per IP
    assert allow_live_call("1.2.3.4", now=3599.0, limit=2) is False
    assert allow_live_call("1.2.3.4", now=3600.0, limit=2) is True  # oldest call left the hour
    assert allow_live_call("9.9.9.9", now=0.0, limit=0) is False
    assert all(allow_live_call("9.9.9.9", now=0.0, limit=-1) for _ in range(50))


def test_allow_live_call_reads_the_setting(monkeypatch):
    _env(monkeypatch, TK_LIVE_LLM_PER_IP_HOUR="1")
    assert allow_live_call("10.0.0.1", now=0.0) is True
    assert allow_live_call("10.0.0.1", now=1.0) is False
    _env(monkeypatch, TK_LIVE_LLM_PER_IP_HOUR="")
    assert get_settings().live_llm_per_ip_hour == 10


def test_interfaces_are_importable_with_contract_names():
    assert issubclass(AnthropicProvider, LLMProvider) and issubclass(OpenAICompatProvider, LLMProvider)
    assert LLMTurn().stop == "end" and LLMTurn().tool_calls == []
