"""Review fixes (fixer, 2026-10-23) in the agent layer: per-task tool allowlists, cache scrubbing, shipped
recordings (export -> load -> keyless replay identity). Fake clients only; nothing touches the network."""

import json
import sys
from pathlib import Path

import pytest
from engine_fakes import DEMO_CIRCLE
from openai.types.chat import ChatCompletion

from app.agent import llm, treasurer
from app.agent.llm import (
    LLMTurn,
    OpenAICompatProvider,
    ToolCall,
    TurnCache,
    collect_volatile_values,
    get_llm_provider,
    scrub_text,
)
from app.agent.treasurer import AgentTask, run_task
from app.config import get_settings

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import export_llm_cache  # noqa: E402


def _completion(content=None, calls=None) -> ChatCompletion:
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)
        ]
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
        }
    )


class _Client:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.chat = self.completions = self

    async def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


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


# --- finding 5: a recover run holds no money tool, and a hidden tool is never executed ---------------------


def test_allowlists_keep_money_tools_out_of_recovery():
    recover = treasurer.allowed_tools("recover")
    assert not recover & {"collect_round", "charge_vaulted_member", "create_pot_payout", "execute_remedies"}
    assert {"propose_remedies", "open_vote", "request_organizer_approval", "record_commitment"} <= recover
    for kind in ("collect_round", "payout", "recover"):
        assert not treasurer.allowed_tools(kind) & {"execute_remedies", "reschedule_turns"}
    assert "charge_vaulted_member" not in treasurer.allowed_tools("payout")


async def test_recover_task_cannot_call_a_money_tool_even_by_name(fake_engine, collector):
    client = _Client(
        [
            _completion(
                None,
                [
                    ("charge_vaulted_member", {"circle_id": DEMO_CIRCLE, "round_n": 3, "member": "ravi"}),
                    ("create_pot_payout", {"circle_id": DEMO_CIRCLE, "round_n": 3}),
                    ("execute_remedies", {"circle_id": DEMO_CIRCLE, "remedy_set_id": "rs:ravi:r3"}),
                ],
            ),
            _completion("I may only propose; people decide."),
        ]
    )
    provider = OpenAICompatProvider(name="groq", model="openai/gpt-oss-120b", client=client)
    task = AgentTask(kind="recover", circle_id=DEMO_CIRCLE, round_n=3, sid="s", context={"member": "ravi"})
    result = await run_task(task, publish=collector, provider=provider)

    offered = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert offered == set(treasurer.allowed_tools("recover"))
    assert fake_engine.calls_named("charge_member") == [] and fake_engine.calls_named("pay_round") == []
    tool_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 3
    assert all(json.loads(m["content"])["error"] == treasurer.TOOL_NOT_ALLOWED for m in tool_msgs)
    assert result.ok is True and result.mode == "live"


# --- finding 8: recorded turns carry no session ids ---------------------------------------------------------


def test_volatile_values_are_collected_and_scrubbed():
    found = collect_volatile_values(
        json.dumps(
            {
                "circle": {"id": "abc123", "run_id": "k3j9qq2a"},
                "payout": {
                    "batch_id": "7XK3B2ZQ9LMNP",
                    "receiver": "dev-sbx@example.com",
                    "ts": "2026-10-23",
                },
                "remedy_ids": ["rm_0a1b2c3d"],
                "member": "ravi",
            }
        ),
        {},
        keep="abc123",
    )
    assert found == {
        "k3j9qq2a": "{run_id}",
        "7XK3B2ZQ9LMNP": "{batch_id}",
        "dev-sbx@example.com": "{receiver}",
        "rm_0a1b2c3d": "{remedy_ids}",
    }
    text = "Pot paid to dev, batch 7XK3B2ZQ9LMNP, receiver dev-sbx@example.com (run k3j9qq2a) in abc123."
    assert scrub_text(text, found) == (
        "Pot paid to dev, batch {batch_id}, receiver {receiver} (run {run_id}) in abc123."
    )


def test_turn_cache_put_stores_scrubbed_text_and_note(monkeypatch, clean_cache):
    monkeypatch.setenv("TK_LLM_CACHE", "auto")
    get_settings.cache_clear()
    cache = TurnCache("auto")
    turn = LLMTurn(
        text="Batch 7XK3B2ZQ9LMNP sent.",
        tool_calls=[
            ToolCall(id="c0", name="record_note", arguments={"circle_id": "abc123", "text": "7XK3B2ZQ9LMNP"})
        ],
        stop="tool_use",
    )
    cache.put(
        "k1", turn, provider="groq", model="m", circle_id="abc123", scrub={"7XK3B2ZQ9LMNP": "{batch_id}"}
    )
    got = cache.get("k1", circle_id="zzz999")
    assert got.text == "Batch {batch_id} sent."
    assert got.tool_calls[0].arguments == {"circle_id": "zzz999", "text": "{batch_id}"}


# --- finding 7: recordings ship with the app -----------------------------------------------------------------


def test_export_load_and_keyless_identity(monkeypatch, clean_cache, tmp_path):
    monkeypatch.setenv("TK_LLM_CACHE", "auto")
    get_settings.cache_clear()
    cache = TurnCache("auto")
    cache.put("good", LLMTurn(text="Covered round 3.", stop="end"), provider="gemini", model="gemini-x")
    cache.put("leaky", LLMTurn(text="Mail ravi@example.com", stop="end"), provider="gemini", model="gemini-x")
    out = tmp_path / "llm_cache.jsonl"

    result = export_llm_cache.export(out)
    assert result["written"] == 1 and [s["reason"] for s in result["skipped"]] == ["email address"]
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["key"] == "good" and (rows[0]["provider"], rows[0]["model"]) == ("gemini", "gemini-x")
    assert export_llm_cache.unsafe_reason('{"text": "order 5O190127TN364715T"}') == "raw PayPal id"

    # A fresh deploy (empty DB): the startup load inserts what is missing, once.
    from sqlmodel import delete

    from app.db import session_scope
    from app.models import LlmCache

    with session_scope() as db:
        db.exec(delete(LlmCache))
    assert llm.load_shipped_cache(out) == 1 and llm.load_shipped_cache(out) == 0
    assert TurnCache("replay").get("good").text == "Covered round 3."

    # No key: the replay identity follows the recording (gemini), not the groq default.
    monkeypatch.setattr(llm, "SHIPPED_CACHE_PATH", out)
    for name in ("TK_LLM_PROVIDER", "TK_LLM_API_KEY", "TK_LLM_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    provider = get_llm_provider(get_settings())
    assert provider is not None and provider.live is False
    assert (provider.name, provider.model) == ("gemini", "gemini-x")

    # An explicit TK_LLM_PROVIDER still wins.
    monkeypatch.setenv("TK_LLM_PROVIDER", "groq")
    get_settings.cache_clear()
    assert get_llm_provider(get_settings()).name == "groq"


def test_missing_shipped_file_changes_nothing(tmp_path):
    assert llm.read_shipped_cache(tmp_path / "none.jsonl") == []
    assert llm.shipped_identity(tmp_path / "none.jsonl") is None
    assert llm.load_shipped_cache(tmp_path / "none.jsonl") == 0
