"""The Anthropic loop against a fake client whose messages.create returns scripted responses. No network."""

import json

import anthropic
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from engine_fakes import DEMO_CIRCLE

from app.agent import treasurer
from app.agent.treasurer import AgentRunResult, AgentTask, run_task


def _message(content, stop_reason, **extra) -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-opus-5-5",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=5),
        **extra,
    )


def _tool_use(id_: str, name: str, **args) -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=id_, name=name, input=args)


def _text(text: str) -> TextBlock:
    return TextBlock(type="text", text=text)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        # The loop keeps appending to the same `messages` list, so snapshot what this call saw.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.closed = False

    async def close(self):
        self.closed = True


def _task(kind="collect_round") -> AgentTask:
    return AgentTask(kind=kind, circle_id=DEMO_CIRCLE, round_n=3, sid="sid-1", context={"source": "test"})


SCRIPT_CLEAN = [
    _message(
        [_text("Reading the board first."), _tool_use("toolu_1", "get_circle", circle_id=DEMO_CIRCLE)],
        "tool_use",
    ),
    _message(
        [
            _tool_use("toolu_2", "collect_round", circle_id=DEMO_CIRCLE, round_n=3),
            _tool_use("toolu_3", "record_note", circle_id=DEMO_CIRCLE, text="Collecting round 3 now."),
        ],
        "tool_use",
    ),
    _message([_text("Round 3 collected: six captures. Pot held until confirmations.")], "end_turn"),
]


async def test_live_loop_runs_scripted_tool_calls(anthropic_key, fake_engine, collector):
    client = FakeClient(SCRIPT_CLEAN)
    result = await run_task(_task(), publish=collector, client=client)

    assert isinstance(result, AgentRunResult)
    assert result.mode == "live" and result.ok is True and result.tool_calls == 3
    assert result.summary == "Round 3 collected: six captures. Pot held until confirmations."
    assert fake_engine.calls_named("collect_round") == [{"circle_id": DEMO_CIRCLE, "round_n": 3}]
    assert client.closed is False  # injected clients are not closed by the loop

    calls = client.messages.calls
    assert len(calls) == 3
    first = calls[0]
    # --- API rules for claude-opus-5-5 ---
    assert first["model"] == "claude-opus-5-5"
    assert first["max_tokens"] == 8000
    assert first["output_config"] == {"effort": "medium"}
    assert first["tool_choice"] == {"type": "auto"}
    for forbidden in ("thinking", "temperature", "top_p", "top_k", "stream"):
        assert forbidden not in first
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "You propose. The policy engine decides. Humans approve." in first["system"][0]["text"]
    assert "ravi (Ravi, hi, PAYPAL)" in first["system"][0]["text"]  # charter summary injected
    tools = first["tools"]
    assert len(tools) == 10 and all(t["strict"] is True for t in tools)
    assert all(t["input_schema"]["additionalProperties"] is False for t in tools)
    assert all(set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"]) for t in tools)
    assert first["messages"] == [{"role": "user", "content": treasurer.task_prompt(_task())}]
    assert "circle_id: abc123" in first["messages"][0]["content"]

    # --- append-only history, one user message carrying every tool_result ---
    second = calls[1]["messages"]
    assert len(second) == 3
    assert second[1] == {"role": "assistant", "content": SCRIPT_CLEAN[0].content}
    assert second[1]["content"] is SCRIPT_CLEAN[0].content  # verbatim, not re-serialized
    assert second[2]["role"] == "user" and [b["tool_use_id"] for b in second[2]["content"]] == ["toolu_1"]
    board = json.loads(second[2]["content"][0]["content"])
    assert board["ok"] is True and board["circle"]["amount"] == "200.00"
    assert second[2]["content"][0]["is_error"] is False

    third = calls[2]["messages"]
    assert len(third) == 5 and third[:3] == second
    assert third[3]["content"] is SCRIPT_CLEAN[1].content
    results = third[4]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in results] == ["toolu_2", "toolu_3"]
    collected = json.loads(results[0]["content"])
    assert collected["ok"] is True and len(collected["results"]) == 6
    assert json.loads(results[1]["content"]) == {"ok": True, "chars": 23}

    # --- trace: every text, tool call and tool result published, results truncated to 400 chars ---
    kinds = collector.kinds()
    assert kinds[:2] == ["system", "text"]  # "Live mode" banner, then the model's first text
    assert kinds.count("tool_call") == 3 and kinds.count("tool_result") == 3
    assert kinds[-1] == "text"
    tool_results = [e for e in collector.events if e.get("kind") == "tool_result"]
    assert all(len(e["text"]) <= 400 for e in tool_results)
    assert tool_results[0]["tool"] == "get_circle" and "ms" in tool_results[0]
    note_events = [e for e in collector.events if e.get("source") == "note"]
    assert note_events and note_events[0]["text"] == "Collecting round 3 now."
    assert result.trace == [
        e for e in collector.events if e.get("type") == "trace" and e.get("source") != "note"
    ]


async def test_unknown_tool_is_reported_to_the_model_not_raised(anthropic_key, fake_engine, collector):
    script = [
        _message([_tool_use("toolu_x", "delete_everything", circle_id=DEMO_CIRCLE)], "tool_use"),
        _message([_text("That tool does not exist; stopping.")], "end_turn"),
    ]
    client = FakeClient(script)
    result = await run_task(_task(), publish=collector, client=client)
    assert result.ok is True and result.tool_calls == 1
    tool_result = client.messages.calls[1]["messages"][2]["content"][0]
    assert tool_result["is_error"] is True and "delete_everything" in tool_result["content"]


async def test_missing_api_key_goes_straight_to_rules_mode(
    no_anthropic_key, fake_engine, collector, monkeypatch
):
    monkeypatch.setattr(
        treasurer,
        "_make_client",
        lambda key: (_ for _ in ()).throw(AssertionError("must not build a client")),
    )
    result = await run_task(_task(), publish=collector)
    assert result.mode == "rules" and result.ok is True
    assert collector.tools_called()[0] == "collect_round"
    assert "ANTHROPIC_API_KEY not set" in collector.events[0]["text"]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_refusal_and_max_tokens_fall_back_to_rules(anthropic_key, fake_engine, collector, stop_reason):
    client = FakeClient([_message([_text("partial")], stop_reason)])
    result = await run_task(_task(), publish=collector, client=client)
    assert result.mode == "rules" and result.ok is True
    assert fake_engine.calls_named("pay_round") == [{"circle_id": DEMO_CIRCLE, "round_n": 3}]
    fallback = [e for e in collector.events if e.get("kind") == "system" and "Falling back" in e["text"]]
    assert fallback and f"stop_reason={stop_reason}" in fallback[0]["text"]
    assert result.trace[0]["mode"] == "live" and result.trace[-1]["mode"] == "rules"


async def test_api_error_falls_back_to_rules(anthropic_key, fake_engine, collector):
    class FakeAPIError(anthropic.APIError):
        def __init__(self):
            Exception.__init__(self, "simulated 529 overloaded")

    client = FakeClient([FakeAPIError()])
    result = await run_task(_task(), publish=collector, client=client)
    assert result.mode == "rules" and result.ok is True
    assert any(
        "Anthropic API error: FakeAPIError" in e["text"]
        for e in collector.events
        if e.get("kind") == "system"
    )


async def test_tool_call_cap_stops_the_loop(anthropic_key, fake_engine, collector):
    script = [
        _message([_tool_use(f"toolu_{i}", "get_circle", circle_id=DEMO_CIRCLE)], "tool_use")
        for i in range(26)
    ]
    client = FakeClient(script)
    result = await run_task(_task(), publish=collector, client=client)
    assert result.mode == "live" and result.ok is False and result.tool_calls == 25
    assert len(client.messages.calls) == 26
    assert "cap" in result.summary.lower()
    assert len(fake_engine.calls_named("board_model")) == 25


async def test_loop_builds_real_client_when_none_given(anthropic_key, fake_engine, collector, monkeypatch):
    built = {}

    def fake_make(key):
        built["key"] = key
        return FakeClient([_message([_text("done")], "end_turn")])

    monkeypatch.setattr(treasurer, "_make_client", fake_make)
    result = await run_task(_task("payout"), publish=collector)
    assert built["key"] == "test-key-not-real"
    assert result.mode == "live" and result.summary == "done" and result.tool_calls == 0


def test_real_client_factory_uses_the_settings_key():
    client = treasurer._make_client("test-key-not-real")
    assert isinstance(client, anthropic.AsyncAnthropic)
    assert client.api_key == "test-key-not-real"


def test_prompt_is_under_600_words_and_has_the_rules():
    template = treasurer.load_prompt_template()
    assert len(template.split()) < 600
    assert "{{charter_summary}}" in template
    rendered = treasurer.render_system_prompt("- Circle: Temple Circle A")
    assert "{{" not in rendered and "- Circle: Temple Circle A" in rendered
    for phrase in ("One round at a time", "get_order_details", "get_available_balance", "denied"):
        assert phrase in rendered


async def test_charter_summary_degrades_without_engine(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "app.engine.state", None)
    text = await treasurer.charter_summary(DEMO_CIRCLE)
    assert "Charter not loaded" in text
