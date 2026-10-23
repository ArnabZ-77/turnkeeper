"""M2 recovery path end to end through the HTTP API (PLAN.md section 3 steps 9-12).

Ravi's round-3 charge is declined (sandbox-injected) -> Ravi replies in Hindi -> Start recovery proposes the
charter-default remedies (buffer advance + invoice due on his payday + turn swap with tomas) -> the affected
members consent, the circle votes, Priya approves -> execution covers Ravi from the buffer, swaps the turns,
re-signs the mandate, sends the invoice through the Agent Toolkit and pays the round-3 pot -> the invoice is
marked paid ("covered, not missed").

The sandbox is mocked with respx (tests/integration/conftest.py); the Agent Toolkit's invoice tools are faked
at `ToolkitAdapter.run` (the toolkit uses `requests`, which respx cannot see). Two runs:

1. No LLM key at all: rules mode drives every step.
2. A fake OpenAI-compatible provider (the free Groq/Gemini path) is injected by monkeypatching the provider
   factory. The real `OpenAICompatProvider` and the official `openai` SDK run against an `httpx.MockTransport`
   that scripts the model's tool calls, proving the agent drives the same MCP tools. No network is used.
"""

from __future__ import annotations

import base64
import json
import re
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from integration.conftest import (
    NET,
    PAYOUT_FEE,
    POT,
    SandboxMock,
    board_of,
    contributions,
    httpx_get_order_details,
    recent_events,
    round_row,
    start_session,
    toggle_ravi,
    wait_for_board,
    wait_for_task,
)

ROTATION_BEFORE = ["meena", "asha", "dev", "lin", "ravi", "tomas"]
ROTATION_AFTER = ["meena", "asha", "dev", "lin", "tomas", "ravi"]
FAKE_INVOICE_ID = "INV2-TEST-0001"
FAKE_QR = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(120))).decode()
OTHERS = ["asha", "meena", "tomas", "lin", "dev"]


class FakeToolkit:
    """`ToolkitAdapter.run` stand-in: orders go through the respx'd httpx shim, invoices are recorded here."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str | None]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.paypal import toolkit_adapter

        fake = self

        async def run(adapter: Any, method: str, params: dict, *, request_id: str | None = None) -> Any:
            if method == "get_order_details":
                return await httpx_get_order_details(adapter, method, params, request_id=request_id)
            fake.calls.append((method, params, request_id))
            if method == "create_invoice":
                return {
                    "rel": "self",
                    "href": f"https://api-m.sandbox.paypal.com/v2/invoicing/invoices/{FAKE_INVOICE_ID}",
                    "method": "GET",
                }
            if method == "send_invoice":
                return {"href": f"https://www.sandbox.paypal.com/invoice/p/#{FAKE_INVOICE_ID}"}
            if method == "generate_invoice_qr_code":
                return {"result": FAKE_QR}
            raise AssertionError(f"unexpected toolkit method {method}")

        monkeypatch.setattr(toolkit_adapter.ToolkitAdapter, "run", run)

    def methods(self) -> list[str]:
        return [m for m, _, _ in self.calls]


@pytest.fixture
def toolkit(monkeypatch: pytest.MonkeyPatch) -> FakeToolkit:
    tk = FakeToolkit()
    tk.install(monkeypatch)
    return tk


# --- the fake OpenAI-compatible model ------------------------------------------------------------


class ScriptedModel:
    """Answers /chat/completions like Groq would, with tool calls scripted per task kind and step."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.payday: str | None = None  # set by the test once Ravi has replied

    # helpers over the request the SDK sent
    @staticmethod
    def _task(messages: list[dict]) -> tuple[str, str]:
        first = next(m["content"] for m in messages if m["role"] == "user")
        kind = re.search(r"^Task: (\w+)", first, re.M).group(1)
        circle_id = re.search(r"^circle_id: (\w+)", first, re.M).group(1)
        return kind, circle_id

    @staticmethod
    def _last_tool_text(messages: list[dict]) -> str:
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        return tool_msgs[-1]["content"] if tool_msgs else ""

    def _plan(self, kind: str, cid: str, step: int, messages: list[dict]) -> list[tuple[str, dict]] | str:
        if kind == "collect_round":
            steps: list[Any] = [
                [("collect_round", {"circle_id": cid, "round_n": 3})],
                [("get_order_details", {"circle_id": cid, "member": m, "round_n": 3}) for m in OTHERS],
                [("get_available_balance", {"circle_id": cid})],
                "Five members paid and their captures are confirmed. Ravi's bank declined the charge, so "
                "the round-3 pot is held until the circle decides how to cover it.",
            ]
            return steps[step]
        assert kind == "recover", kind
        if step == 0:
            return [("list_inbox_replies", {"circle_id": cid, "member": "ravi"})]
        if step == 1:
            assert "member_reply" in self._last_tool_text(messages)  # replies arrive framed as data
            assert self.payday, "the test sets Ravi's payday before starting the recovery"
            return [
                (
                    "record_commitment",
                    {"circle_id": cid, "member": "ravi", "amount": "200.00", "date": self.payday},
                )
            ]
        if step == 2:
            return [("propose_remedies", {"circle_id": cid, "round_n": 3, "member": "ravi", "remedies": []})]
        tool_texts = " ".join(str(m.get("content") or "") for m in messages if m["role"] == "tool")
        set_id = re.search(r'"remedy_set_id": ?"([^"]+)"', tool_texts).group(1)
        assert set_id == "rs:ravi:r3"  # the model sees the stable logical handle, never the raw random id
        if step == 3:
            return [("open_vote", {"circle_id": cid, "remedy_set_id": set_id})]
        if step == 4:
            return [("request_organizer_approval", {"circle_id": cid, "remedy_set_id": set_id})]
        return (
            "Ravi says his salary comes on Friday. I proposed covering round 3 from the buffer, an invoice "
            "due Friday and moving his turn after Tomas. Ravi and Tomas must consent, four members must vote "
            "yes and Priya approves."
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "fake-llm.test" and request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-not-a-real-key"
        body = json.loads(request.content)
        self.requests.append(body)
        assert body["model"] == "fake-oss-model" and body["tool_choice"] == "auto"
        assert body["messages"][0]["role"] == "system"
        names = {t["function"]["name"] for t in body["tools"]}
        assert all(t["type"] == "function" for t in body["tools"])
        kind, cid = self._task(body["messages"])
        # Per-task allowlists: a recover run (member text in context) holds no money tool at all.
        assert not names & {"execute_remedies", "reschedule_turns"}
        if kind == "recover":
            assert {"propose_remedies", "open_vote"} <= names
            assert not names & {"collect_round", "charge_vaulted_member", "create_pot_payout"}
        else:
            assert "collect_round" in names and "propose_remedies" not in names
        step = sum(1 for m in body["messages"] if m["role"] == "assistant")
        plan = self._plan(kind, cid, step, body["messages"])
        if isinstance(plan, str):
            message: dict[str, Any] = {"role": "assistant", "content": plan}
            finish = "stop"
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{step}_{i}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                    for i, (name, args) in enumerate(plan)
                ],
            }
            finish = "tool_calls"
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{len(self.requests)}",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedModel:
    """Inject an OpenAI-compatible provider (the real class + the real openai SDK) over a mock transport."""
    import openai

    import app.agent.treasurer as treasurer
    from app.agent.llm import OpenAICompatProvider

    model = ScriptedModel()

    def factory(settings: Any = None, *, live: bool = True) -> OpenAICompatProvider:
        client = openai.AsyncOpenAI(
            api_key="test-not-a-real-key",
            base_url="https://fake-llm.test/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(model)),
        )
        return OpenAICompatProvider(name="groq", model="fake-oss-model", client=client, live=live)

    monkeypatch.setattr(treasurer, "get_llm_provider", factory)
    return model


# --- the shared storyboard -------------------------------------------------------------------------


def judge(client: TestClient, action: str, circle_id: str, **extra: Any) -> dict:
    r = client.post(f"/api/judge/{action}", json={"circle_id": circle_id, **extra})
    assert r.status_code == 200, (action, r.text)
    return r.json()


def recovery_of(client: TestClient, circle_id: str) -> dict:
    r = client.get(f"/api/circles/{circle_id}/recovery")
    assert r.status_code == 200, r.text
    return r.json()


def trace_tools(sid: str) -> list[str]:
    return [e["tool"] for e in recent_events(sid, "trace") if e.get("kind") == "tool_call"]


def run_storyboard(
    client: TestClient, sandbox: SandboxMock, toolkit: FakeToolkit, *, expect_mode: str, model=None
) -> dict:
    sess = start_session(client)
    cid, sid = sess["circle_id"], sess["sid"]
    board = board_of(client, cid)
    assert board["circle"]["rotation"] == ROTATION_BEFORE
    assert round_row(board, 3)["recipient"] == "dev"
    toggle_ravi(client, cid, on=True)

    # 1. collect round 3: ravi is declined, the pot is held, and Ravi is asked right away when he can pay
    r = client.post(f"/api/circles/{cid}/rounds/3/collect")
    assert r.status_code == 202, r.text
    done = wait_for_task(client, r.json()["task_id"])
    assert done["mode"] == expect_mode, done
    rows = contributions(board_of(client, cid), 3)
    assert rows["ravi"]["status"] == "DECLINED" and rows["ravi"]["decline_code"] == "INSTRUMENT_DECLINED"
    assert all(rows[m]["status"] == "COMPLETED" for m in OTHERS)
    assert sandbox.payouts == []
    assert sandbox.get_order_route.call_count == 5  # every capture confirmed through the toolkit shim
    view = recovery_of(client, cid)
    assert view["status"] == "none"
    assert [(m["member"], m["direction"], m["round_n"]) for m in view["inbox"]] == [("ravi", "out", 3)]

    # 2. Ravi replies (Hindi: "salary comes Friday, I'll pay then")
    reply = judge(client, "reply_as_ravi", cid)
    payday = reply["payday"]
    assert payday and reply["translation"]
    if model is not None:
        model.payday = payday

    # 3. start the recovery
    r = client.post(f"/api/circles/{cid}/recovery/start", json={})
    assert r.status_code == 202, r.text
    assert r.json()["member"] == "ravi" and r.json()["round_n"] == 3
    done = wait_for_task(client, r.json()["task_id"])
    assert done["ok"] is True and done["mode"] == expect_mode, done
    view = recovery_of(client, cid)
    assert view["source"] == ("agent" if expect_mode == "live" else "rules")
    assert view["status"] == "voting" and view["member"] == "ravi" and view["round_n"] == 3
    assert view["problems"] == []
    assert view["commitment"]["date"] == payday and view["commitment"]["amount"] == "200.00"
    remedies = {rm["type"]: rm for rm in view["remedies"]}
    assert set(remedies) == {"buffer_advance", "repay_by_invoice", "turn_swap"}
    assert remedies["buffer_advance"]["params"]["amount"] == str(NET)
    assert remedies["repay_by_invoice"]["params"]["due_date"] == payday
    assert remedies["turn_swap"]["params"]["swap_with"] == "tomas"
    assert view["approval"]["status"] == "requested"
    directions = [m["direction"] for m in view["inbox"]]
    assert directions[:2] == ["out", "in"]  # the notice comes before the reply it asked for

    # 4. approval is refused until the people have spoken
    r = client.post("/api/judge/approve_as_priya", json={"circle_id": cid})
    assert r.status_code == 409, r.text

    # 5. consents (ravi + tomas), then votes until quorum (4/6)
    consents = judge(client, "consent_as_affected", cid)
    assert sorted(consents["consented"]) == ["ravi", "tomas"]
    votes = judge(client, "vote_as_members", cid)
    assert votes["votes"]["yes"] >= 4 and votes["votes"]["passed"] is True

    # 6. Priya's one tap executes: advance -> swap -> invoice -> pay the settled round
    approved = judge(client, "approve_as_priya", cid)
    assert approved["ok"] is True, approved
    results = {res["type"]: res for res in approved["results"]}
    assert (
        results["buffer_advance"]["ok"] and results["turn_swap"]["ok"] and results["repay_by_invoice"]["ok"]
    )
    assert results["repay_by_invoice"]["invoice_id"] == FAKE_INVOICE_ID
    assert results["repay_by_invoice"]["simulated"] is False
    assert toolkit.methods() == ["create_invoice", "send_invoice", "generate_invoice_qr_code"]
    invoice_params = toolkit.calls[0][1]
    assert invoice_params["items"][0]["unit_amount"]["value"] == "200.00"
    assert all(rid and rid.startswith(f"tk:{sess['run_id']}:{cid}:3:ravi:inv") for _, _, rid in toolkit.calls)

    board = wait_for_board(client, cid, lambda b: round_row(b, 3)["status"] == "PAID")
    rows = contributions(board, 3)
    assert rows["ravi"]["status"] == "COVERED"
    assert board["circle"]["rotation"] == ROTATION_AFTER
    assert round_row(board, 5)["recipient"] == "tomas" and round_row(board, 6)["recipient"] == "ravi"
    assert board["counters"]["shortfalls_covered"] == 1
    assert board["recovery"]["status"] == "executed"
    assert board["recovery"]["invoice"]["id"] == FAKE_INVOICE_ID
    assert board["recovery"]["invoice"]["simulated"] is False and board["recovery"]["invoice"]["qr"]
    labels = {m["handle"]: m["label"] for m in board["members"]}
    assert labels["ravi"] and "covered by buffer" in labels["ravi"].lower()

    # the round-3 pot: five captured nets plus the buffer advance, less the payout fee, to dev
    r3 = round_row(board, 3)
    assert r3["payout"]["status"] == "SUCCESS" and r3["payout"]["amount"] == str(POT)
    advance = Decimal(remedies["buffer_advance"]["params"]["amount"])
    assert Decimal(r3["payout"]["amount"]) == NET * 5 + advance - PAYOUT_FEE
    assert len(sandbox.payouts) == 1
    sent = sandbox.payouts[0]["item"]
    assert sent["receiver"] == "dev@example.com" and sent["amount"]["value"] == str(POT)
    assert any(
        e["type"] == "schedule_changed" and e["rotation"] == ROTATION_AFTER
        for e in recent_events(sid, "schedule_changed")
    )

    # 7. the invoice is paid (Judge Mode simulates INVOICING.INVOICE.PAID)
    paid = judge(client, "mark_invoice_paid", cid)
    assert paid["ok"] is True and paid["simulated"] is True
    board = board_of(client, cid)
    assert contributions(board, 3)["ravi"]["label"] == "Round 3: covered, not missed"
    assert board["recovery"]["status"] == "repaid" and board["recovery"]["invoice"]["status"] == "PAID"

    assert len(sandbox.payout_posts) == 1  # one PayPal payout for round 3, nothing more
    return {"sid": sid, "circle_id": cid}


# --- run 1: no LLM key at all -----------------------------------------------------------------------


def test_recovery_storyboard_in_rules_mode_with_no_llm_key(
    client: TestClient, sandbox: SandboxMock, toolkit: FakeToolkit
):
    from app.config import get_settings

    s = get_settings()
    assert s.llm_configured is False and s.llm_provider == "none"
    assert client.get("/healthz").json()["llm_configured"] is False
    out = run_storyboard(client, sandbox, toolkit, expect_mode="rules")
    tools = trace_tools(out["sid"])
    assert "collect_round" in tools and "create_pot_payout" not in tools  # held, then paid by the remedy
    assert all(
        e.get("mode") in (None, "rules")
        for e in recent_events(out["sid"], "trace")
        if e.get("kind") in ("tool_call", "tool_result")
    )


# --- run 2: a fake OpenAI-compatible provider drives the same tools ---------------------------------


def test_recovery_storyboard_driven_by_an_openai_compatible_model(
    client: TestClient, sandbox: SandboxMock, toolkit: FakeToolkit, fake_llm: ScriptedModel
):
    out = run_storyboard(client, sandbox, toolkit, expect_mode="live", model=fake_llm)
    tools = trace_tools(out["sid"])
    expected = [
        "collect_round",
        *["get_order_details"] * 5,
        "get_available_balance",
        "list_inbox_replies",
        "record_commitment",
        "propose_remedies",
        "open_vote",
        "request_organizer_approval",
    ]
    assert tools == expected, tools
    assert "execute_remedies" not in tools  # execution only after people vote, consent and approve
    live = [e for e in recent_events(out["sid"], "trace") if e.get("kind") in ("tool_call", "tool_result")]
    assert live and all(e.get("mode") == "live" for e in live if e.get("source") != "recovery"), [
        (e.get("tool"), e.get("mode"), e.get("source")) for e in live if e.get("mode") != "live"
    ]
    assert len(fake_llm.requests) == 4 + 6  # collect: 3 tool turns + 1 summary; recover: 5 + 1
