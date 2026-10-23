"""Rules mode end to end over the real internal MCP server with the engine faked."""

from engine_fakes import DEMO_CIRCLE, PolicyDenied

from app.agent.rules_mode import run_rules
from app.agent.treasurer import AgentRunResult, AgentTask


def _task(kind: str = "collect_round", round_n: int = 3) -> AgentTask:
    return AgentTask(kind=kind, circle_id=DEMO_CIRCLE, round_n=round_n, sid="sid-1")


async def test_clean_round_collects_confirms_checks_balance_and_pays(fake_engine, collector):
    result = await run_rules(_task(), publish=collector)
    assert isinstance(result, AgentRunResult)
    assert result.mode == "rules" and result.ok is True
    assert collector.tools_called() == [
        "collect_round",
        *["get_order_details"] * 6,
        "get_round_matrix",
        "get_available_balance",
        "create_pot_payout",
        "record_note",
    ]
    assert result.tool_calls == 11
    confirmed = [kw["member"] for kw in fake_engine.calls_named("confirm_capture")]
    assert confirmed == ["asha", "ravi", "meena", "tomas", "lin", "dev"]
    assert fake_engine.calls_named("pay_round") == [{"circle_id": DEMO_CIRCLE, "round_n": 3}]
    assert "Pot paid: 1155.18 to dev via PAYPAL (status SUCCESS" in result.summary
    assert "Confirmed 6/6 captures" in result.summary
    # honest trace: first event says rules mode, every tool call has a result, final text summary
    first = collector.events[0]
    assert first["type"] == "trace" and first["kind"] == "system" and first["text"].startswith("Rules mode")
    assert all(e["mode"] == "rules" for e in result.trace)
    kinds = collector.kinds()
    assert kinds.count("tool_call") == kinds.count("tool_result") == 11
    assert kinds[-1] == "text" and collector.events[-1]["text"] == result.summary
    # engine chips flowed through the same publisher
    assert sum(1 for e in collector.events if e.get("type") == "chip") == 6
    # the tracer's events are exactly the published trace events, apart from the note the tool itself publishes
    assert result.trace == [
        e for e in collector.events if e.get("type") == "trace" and e.get("source") != "note"
    ]


async def test_declined_member_holds_the_payout(fake_engine_factory, collector):
    engine = fake_engine_factory(declines=("ravi",))
    result = await run_rules(_task(), publish=collector)
    assert result.ok is True and result.mode == "rules"
    tools = collector.tools_called()
    assert tools.count("get_order_details") == 5
    assert "create_pot_payout" not in tools
    assert engine.calls_named("pay_round") == []
    assert "1 declined (ravi)" in result.summary
    assert "Payout held: waiting on ravi." in result.summary
    note = [e for e in collector.events if e.get("kind") == "text" and e.get("source") == "note"]
    assert note and "waiting on ravi" in note[0]["text"]


async def test_payout_task_pays_only_when_round_is_settled(fake_engine, collector):
    # rounds 1-2 are settled in the fixture; round 3 is not collected yet
    held = await run_rules(_task("payout", round_n=3), publish=collector)
    assert "create_pot_payout" not in collector.tools_called()
    assert "Payout held: waiting on asha, dev, lin, meena, ravi, tomas." in held.summary
    collector.events.clear()
    paid = await run_rules(_task("payout", round_n=2), publish=collector)
    assert collector.tools_called() == [
        "get_round_matrix",
        "get_available_balance",
        "create_pot_payout",
        "record_note",
    ]
    assert paid.ok is True and "Pot paid: 1155.18 to asha" in paid.summary


async def test_policy_denial_is_reported_not_raised(fake_engine_factory, collector):
    fake_engine_factory(pay_error=PolicyDenied("mandate missing for this circle"))
    result = await run_rules(_task("payout", round_n=2), publish=collector)
    assert result.ok is False
    assert "Payout not sent: denied by policy: mandate missing for this circle." in result.summary
    denied_trace = [
        e for e in collector.events if e.get("kind") == "tool_result" and e.get("tool") == "create_pot_payout"
    ]
    assert denied_trace and '"denied"' in denied_trace[0]["text"]


async def test_recover_task_reads_history_and_leaves_a_note(fake_engine_factory, collector):
    engine = fake_engine_factory(declines=("ravi",))
    await engine.collect_round(DEMO_CIRCLE, 3, publish=collector)
    result = await run_rules(_task("recover"), publish=collector)
    assert collector.tools_called()[-3:] == ["get_round_matrix", "get_member_history", "record_note"]
    assert engine.calls_named("pay_round") == [] and engine.calls_named("charge_member") == []
    assert "ravi is outstanding" in result.summary and "every 2nd Friday" in result.summary
    assert result.ok is True


async def test_rules_mode_works_without_a_publisher(fake_engine):
    result = await run_rules(_task())
    assert result.ok is True and result.tool_calls == 11 and len(result.trace) >= 23


async def test_engine_missing_is_an_honest_failure(monkeypatch, collector):
    import sys

    from app.mcp import internal

    monkeypatch.delitem(sys.modules, "app.engine.rounds", raising=False)
    monkeypatch.setattr(internal, "_rounds", lambda: (_ for _ in ()).throw(ModuleNotFoundError("app.engine")))
    monkeypatch.setattr(internal, "_state", lambda: (_ for _ in ()).throw(ModuleNotFoundError("app.engine")))
    result = await run_rules(_task(), publish=collector)
    assert result.ok is False and result.mode == "rules"
    assert "Collection failed: ENGINE_UNAVAILABLE" in result.summary
    assert "create_pot_payout" not in collector.tools_called()
