"""Review fixes (fixer, 2026-10-23): cross-session AI replay, logical remedy-set handles, cache scrubbing,
deterministic recovery finish, real-shortfall checks, invoice and webhook guards, single execution,
and the no-sandbox simulated decline. PayPal, the toolkit and the model are faked; no network."""

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest
from openai.types.chat import ChatCompletion
from recovery_fakes import FakeInvoiceToolkit, invoice_paid_event, use_swappable_rotation
from sqlmodel import select

from app.agent import treasurer
from app.agent.llm import OpenAICompatProvider
from app.config import get_settings
from app.db import session_scope
from app.domain import remedies as rd
from app.engine import inbox, recovery, rounds, state, tasks, webhooks
from app.engine.sessions import create_session
from app.models import LedgerRow, Remedy, RemedySetRow


@pytest.fixture
def toolkit(monkeypatch) -> FakeInvoiceToolkit:
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    return tk


async def _declined_clone(collector) -> tuple[str, str]:
    """A fresh session clone with round 3 collected and ravi declined (fixture flag). -> (circle_id, run_id)"""
    from fakes import vault_members

    sess = create_session(enforce_cap=False)
    vault_members(sess.circle_id)
    use_swappable_rotation(sess.circle_id)
    await rounds.collect_round(sess.circle_id, 3, publish=collector)
    return sess.circle_id, sess.run_id


def _ledger(circle_id: str, kind: str) -> list[LedgerRow]:
    return [r for r in state.ledger_rows(circle_id) if r.kind == kind]


# --- a scripted OpenAI-compatible model (the real provider class, a fake SDK client) ---------------------


def _completion(content: str | None = None, calls: list[tuple[str, dict]] | None = None) -> ChatCompletion:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)
        ]
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


class _ScriptClient:
    """``client.chat.completions.create(**kw)`` answering from a script of callables(messages) -> completion."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        step = self.script.pop(0)
        return step(kwargs["messages"]) if callable(step) else step

    async def close(self) -> None:
        return None


def _recover_script(cid: str, run_id: str) -> list:
    handle = "rs:ravi:r3"  # the logical handle: the same in every session
    return [
        _completion(None, [("get_circle", {"circle_id": cid})]),
        _completion(None, [("list_inbox_replies", {"circle_id": cid, "member": "ravi"})]),
        _completion(
            None, [("propose_remedies", {"circle_id": cid, "round_n": 3, "member": "ravi", "remedies": []})]
        ),
        _completion(None, [("open_vote", {"circle_id": cid, "remedy_set_id": handle})]),
        _completion(None, [("request_organizer_approval", {"circle_id": cid, "remedy_set_id": handle})]),
        _completion(f"Run {run_id}: covered round 3 from the buffer; the circle now votes on {handle}."),
    ]


def _llm_env(monkeypatch) -> None:
    monkeypatch.setenv("TK_LLM_PROVIDER", "")
    monkeypatch.setenv("TK_LLM_API_KEY", "")
    monkeypatch.setenv("TK_LLM_MODEL", "")
    monkeypatch.setenv("TK_LLM_CACHE", "auto")
    get_settings.cache_clear()


async def _run_recover(cid: str, run_id: str, collector) -> tasks.TaskRecord:
    rec = tasks.TaskRecord(task_id=f"t-{cid}", kind="recover", circle_id=cid, round_n=3, sid="sid-test")
    await recovery.recover_runner(rec, run_id, collector, False, member="ravi")
    return rec


# --- findings 2, 3 and 8: record in one session, replay in another --------------------------------------


async def test_recover_run_recorded_in_one_session_replays_in_another(
    monkeypatch, paypal, toolkit, collector, tmp_path
):
    _llm_env(monkeypatch)
    cid_a, run_a = await _declined_clone(collector)
    cid_b, run_b = await _declined_clone(collector)
    assert cid_a != cid_b and run_a != run_b

    # Session A: a "free key" run, recorded turn by turn.
    client = _ScriptClient(_recover_script(cid_a, run_a))
    recorder = OpenAICompatProvider(name="groq", model="openai/gpt-oss-120b", client=client)
    monkeypatch.setattr(treasurer, "get_llm_provider", lambda settings, live=True: recorder)
    rec_a = await _run_recover(cid_a, run_a, collector)
    assert rec_a.mode == "live" and rec_a.ok is True and len(client.calls) == 6
    assert run_a not in json.dumps(client.calls[0]["messages"][1])  # the prompt carries no run_id
    assert recovery.latest_set(cid_a).status == "voting"

    # Session B: no key at all. Every turn replays, including the ones after propose_remedies.
    monkeypatch.undo()
    _llm_env(monkeypatch)
    from app.agent import llm

    monkeypatch.setattr(
        llm, "SHIPPED_CACHE_PATH", tmp_path / "none.jsonl"
    )  # undo() dropped the conftest guard
    toolkit_b = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: toolkit_b)
    events = type(collector)()
    rec_b = await _run_recover(cid_b, run_b, events)
    assert rec_b.mode == "replay", [e.get("text") for e in events.of_type("trace")]
    row_b = recovery.latest_set(cid_b)
    assert row_b.source == "agent" and row_b.status == "voting" and row_b.approval_requested_at is not None
    # The replayed closing text never shows session A's run id: it was scrubbed to a placeholder.
    assert run_a not in (rec_b.summary or "") and "{run_id}" in (rec_b.summary or "")
    assert not any(run_a in json.dumps(e) for e in events.events)


def test_task_prompt_drops_session_values():
    task = treasurer.AgentTask(
        kind="recover",
        circle_id="abc123",
        round_n=3,
        sid="s",
        context={"run_id": "zzzz9999", "member": "ravi"},
    )
    prompt = treasurer.task_prompt(task)
    assert "zzzz9999" not in prompt and '"member": "ravi"' in prompt


async def test_logical_handle_resolves_to_the_latest_live_set(declined_circle):
    cid = declined_circle
    first = await recovery.propose(cid, 3, "ravi", None)
    second = await recovery.propose(cid, 3, "ravi", None)  # supersedes the first
    assert recovery.resolve_set_id(cid, "rs:ravi:r3") == second["set_id"] != first["set_id"]
    assert recovery.resolve_set_id(cid, first["set_id"]) == first["set_id"]  # raw ids still pass
    with pytest.raises(LookupError):
        recovery.resolve_set_id(cid, "rs:asha:r3")


@pytest.fixture
async def declined_circle(vaulted_circle, paypal, collector) -> str:
    use_swappable_rotation(vaulted_circle)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    return vaulted_circle


# --- finding 4: the runner always leaves an open vote -----------------------------------------------------


async def test_runner_opens_the_vote_when_the_agent_stopped_after_proposing(
    declined_circle, toolkit, collector, monkeypatch
):
    cid = declined_circle

    async def fake_run_task(task, *, publish, live_allowed=True):
        await recovery.propose(cid, 3, "ravi", None, source="agent", publish=publish)
        # ... then a 429 sent the run to rules mode, which only leaves notes.
        trace = [{"type": "trace", "kind": "tool_call", "mode": "live"}]
        return treasurer.AgentRunResult(ok=True, mode="rules", summary="fell back", tool_calls=1, trace=trace)

    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    rec = await _run_recover(cid, "run00001", collector)
    row = recovery.latest_set(cid)
    assert row.source == "agent" and row.status == "voting" and row.approval_requested_at is not None
    assert rec.mode == "live" and rec.ok is True
    assert any("Rules finished the vote opening" in e.get("text", "") for e in collector.of_type("trace"))
    # Consent and votes are accepted now (they answered 409 on a 'proposed' set before).
    await recovery.record_consent(cid, row.id, "ravi", "yes")


async def test_runner_replaces_an_invalid_agent_proposal_with_the_rules_sequence(
    declined_circle, toolkit, collector, monkeypatch
):
    cid = declined_circle

    async def fake_run_task(task, *, publish, live_allowed=True):
        out = await recovery.propose(
            cid, 3, "ravi", [{"type": "turn_swap", "swap_with": "tomas", "reason": "x"}], source="agent"
        )
        assert out["status"] == "invalid"
        return treasurer.AgentRunResult(ok=True, mode="live", summary="invalid", tool_calls=1, trace=[])

    monkeypatch.setattr(treasurer, "run_task", fake_run_task)
    rec = await _run_recover(cid, "run00002", collector)
    row = recovery.latest_set(cid)
    assert row.source == "rules" and row.status == "voting" and rec.mode == "rules"


# --- finding 6: only a real decline can be remedied -------------------------------------------------------


async def test_no_remedy_without_a_decline(declined_circle, collector):
    cid = declined_circle
    pending = await recovery.propose(cid, 5, "asha", None)  # asha is PENDING in round 5
    completed = await recovery.propose(cid, 3, "asha", None)  # asha paid round 3
    assert pending["ok"] is False and pending["error"] == "NO_SHORTFALL"
    assert completed["ok"] is False and completed["error"] == "NO_SHORTFALL"
    assert recovery.latest_set(cid) is None and not _ledger(cid, "buffer_advance")
    assert rd.SHORT_STATUSES == frozenset({"DECLINED"})
    charter = state.load_charter(cid)
    cs = recovery.recovery_state(cid, 5)
    problems = rd.validate_remedy_set(charter, cs, rd.propose_default_remedies(charter, cs, "asha"))
    assert any("no shortfall" in p for p in problems)


# --- finding 10: only a sent invoice can be marked paid ---------------------------------------------------


async def _approved_set(cid: str, collector) -> str:
    out = await recovery.run_rules_recovery(cid, publish=collector)
    set_id = out["set_id"]
    for h in recovery.domain_set(recovery.get_set(cid, set_id)).affected_members():
        await recovery.record_consent(cid, set_id, h, "yes")
    for h in ("asha", "meena", "lin", "dev"):
        await recovery.cast_vote(cid, set_id, h, "yes")
    return set_id


async def test_failed_invoice_cannot_be_marked_paid(declined_circle, collector, monkeypatch):
    cid = declined_circle
    tk = FakeInvoiceToolkit(fail_on="send_invoice")
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    set_id = await _approved_set(cid, collector)
    await recovery.approve(cid, set_id, publish=collector)
    assert recovery.current_invoice(cid).invoice_status == "DRAFT"
    with pytest.raises(recovery.RecoveryError, match="not sent"):
        await recovery.simulate_invoice_paid(cid, publish=collector)
    assert not _ledger(cid, "advance_repaid")


# --- finding 12: one set executes once ---------------------------------------------------------------------


async def test_concurrent_approvals_execute_the_set_once(declined_circle, toolkit, collector):
    cid = declined_circle
    set_id = await _approved_set(cid, collector)
    a, b = await asyncio.gather(
        recovery.approve(cid, set_id, publish=collector), recovery.approve(cid, set_id, publish=collector)
    )
    assert sorted([bool(a.get("already")), bool(b.get("already"))]) == [False, True]
    assert recovery.get_set(cid, set_id).status == "executed"
    with session_scope() as db:
        statuses = {r.type: r.status for r in db.exec(select(Remedy).where(Remedy.set_id == set_id)).all()}
    assert statuses == {"buffer_advance": "executed", "turn_swap": "executed", "repay_by_invoice": "executed"}
    assert toolkit.methods().count("create_invoice") == 1 and len(_ledger(cid, "buffer_advance")) == 1


async def test_execution_claims_the_set_before_running(declined_circle, toolkit, collector):
    cid = declined_circle
    set_id = await _approved_set(cid, collector)
    await recovery.approve(cid, set_id, execute=False, publish=collector)
    with session_scope() as db:  # another worker claimed it already
        row = db.get(RemedySetRow, set_id)
        row.status = "executing"
        db.add(row)
    out = await recovery.execute_remedies(cid, set_id, publish=collector)
    assert out["already"] is True and not _ledger(cid, "buffer_advance") and toolkit.calls == []


# --- finding 9: unverified webhooks never move money on a deployed app ---------------------------------------


async def test_unverified_invoice_paid_is_not_dispatched_when_deployed(
    declined_circle, toolkit, collector, monkeypatch
):
    cid = declined_circle
    set_id = await _approved_set(cid, collector)
    await recovery.approve(cid, set_id, publish=collector)
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://turnkeeper.example.invalid")
    get_settings.cache_clear()
    body = json.dumps(invoice_paid_event(event_id="WH-FORGED-1")).encode()
    out = await webhooks.handle_event({}, body, publish_for=lambda _cid: collector)
    assert out["handled"] is False and out["rejected"] == "unverified"
    assert not _ledger(cid, "advance_repaid")

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")  # local development still dispatches
    get_settings.cache_clear()
    body = json.dumps(invoice_paid_event(event_id="WH-LOCAL-1")).encode()
    out = await webhooks.handle_event({}, body, publish_for=lambda _cid: collector)
    assert out["handled"] is True and _ledger(cid, "advance_repaid")


# --- finding 1: a cold judge reaches the recovery path without a live sandbox --------------------------------


async def test_simulated_round_decline_opens_the_recovery_path(circle_id, toolkit, collector):
    use_swappable_rotation(circle_id)
    out = await recovery.simulate_round_decline(circle_id, 3, "ravi", publish=collector)
    assert out["ok"] is True and len(out["captured"]) == 5 and "ravi" not in out["captured"]
    contribs = {c.member_handle: c for c in state.round_contributions(circle_id, 3)}
    ravi = contribs.pop("ravi")
    assert ravi.status == "DECLINED" and ravi.decline_code == "INSTRUMENT_DECLINED"
    assert ravi.decline_source == "simulated" and ravi.label == recovery.SIMULATED_DECLINE_LABEL
    assert all(c.status == "COMPLETED" and c.order_id.startswith("REPLAY-") for c in contribs.values())
    nets = [r for r in _ledger(circle_id, "contribution_net") if r.round_n == 3]
    assert len(nets) == 5 and all(Decimal(r.amount) == Decimal("192.53") for r in nets)
    assert recovery.find_shortfall(circle_id) == (3, "ravi")
    assert inbox.list_messages(circle_id, member="ravi", direction="out")  # the decline notice went out
    again = await recovery.simulate_round_decline(circle_id, 3, "ravi", publish=collector)
    assert again["already"] is True

    # The whole story runs on it; the payout is skipped because no live charge happened.
    set_id = await _approved_set(circle_id, collector)
    result = await recovery.approve(circle_id, set_id, publish=collector)
    assert result["ok"] is True and result["status"] == "executed"
    assert result["payout"]["skipped"].startswith("simulated round")
    paid = await recovery.simulate_invoice_paid(circle_id, publish=collector)
    assert paid["handled"] is True and _ledger(circle_id, "advance_repaid")
