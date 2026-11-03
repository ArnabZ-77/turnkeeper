"""Member capabilities: the pure token (app.domain.capability), the engine rules (app.engine.capabilities), the mint
route and the public MCP tools get_request / answer_request / get_my_repayment. PayPal and the LLM are faked."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from recovery_fakes import FakeInvoiceToolkit  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.domain import capability as capm  # noqa: E402
from app.engine import capabilities, member_agent, recovery, state, story  # noqa: E402
from app.models import CapabilityUse  # noqa: E402
from routes.test_member_mandates_routes import _enable, _to_voting  # noqa: E402

SECRET = "a-long-test-secret-for-capabilities"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    monkeypatch.setenv("TK_LLM_API_KEY", "")
    monkeypatch.delenv("TK_MEMBER_MANDATES", raising=False)
    monkeypatch.delenv("TK_MEMBER_AGENTS", raising=False)
    get_settings.cache_clear()
    state.clear_paypal_balance()
    yield
    state.clear_paypal_balance()
    get_settings.cache_clear()


@pytest.fixture
def toolkit(monkeypatch) -> FakeInvoiceToolkit:
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    return tk


@pytest.fixture
def voting(client, session, toolkit) -> str:
    cid = _to_voting(client, toolkit)
    _enable(client, cid)
    return cid


def _mint(client, cid: str, member: str, scope: str = "answer") -> str:
    r = client.post(f"/api/circles/{cid}/member-agents/capability", json={"member": member, "scope": scope})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# --- pure token -------------------------------------------------------------------------------------------------


def test_token_round_trip_and_tamper():
    token, cap = capm.mint(
        circle_id="c1",
        member="ravi",
        scope="answer",
        now=1000,
        secret=SECRET,
        set_id="rs_1",
        set_sha="x",
        item="vote",
    )
    assert capm.verify(token, secret=SECRET, now=1001) == cap
    head, body, sig = token.split(".")
    with pytest.raises(capm.CapabilityError) as e:
        capm.verify(f"{head}.{body}.{sig[:-2]}AA", secret=SECRET, now=1001)
    assert e.value.code == capm.CAPABILITY_INVALID
    with pytest.raises(capm.CapabilityError) as e:
        capm.verify(token, secret="another-secret-entirely", now=1001)
    assert e.value.code == capm.CAPABILITY_INVALID
    with pytest.raises(capm.CapabilityError) as e:
        capm.verify("not-a-token", secret=SECRET, now=1001)
    assert e.value.code == capm.CAPABILITY_INVALID


def test_answer_token_expires_after_15_minutes_even_if_asked_for_longer():
    token, cap = capm.mint(
        circle_id="c1",
        member="ravi",
        scope="answer",
        now=0,
        secret=SECRET,
        ttl=99999,
        set_id="s",
        set_sha="x",
        item="vote",
    )
    assert cap.exp == 15 * 60
    with pytest.raises(capm.CapabilityError) as e:
        capm.verify(token, secret=SECRET, now=15 * 60)
    assert e.value.code == capm.CAPABILITY_EXPIRED


def test_scope_checks():
    _, cap = capm.mint(circle_id="c1", member="ravi", scope="read", now=0, secret=SECRET)
    with pytest.raises(capm.CapabilityError) as e:
        capm.require_member(cap, "tomas")
    assert e.value.code == capm.CAPABILITY_SCOPE and "for ravi only" in e.value.message
    with pytest.raises(capm.CapabilityError) as e:
        capm.require_answer(cap, "yes")
    assert e.value.code == capm.CAPABILITY_SCOPE


# --- engine + routes --------------------------------------------------------------------------------------------


def test_mint_route_needs_the_feature_and_the_owning_session(client, session, toolkit):
    cid = _to_voting(client, toolkit)
    r = client.post(f"/api/circles/{cid}/member-agents/capability", json={"member": "tomas"})
    assert r.status_code == 404  # member mandates off for this circle
    assert (
        client.post("/api/circles/nope/member-agents/capability", json={"member": "tomas"}).status_code == 403
    )


async def test_answer_once_with_the_floor_and_the_named_refusals(client, voting):
    token = _mint(client, voting, "tomas")
    view = capabilities.request_view(token)
    assert view["request"]["item"] == "consent" and view["request"]["choices"] == ["yes", "no"]

    with pytest.raises(capm.CapabilityError) as e:  # Tomas's token cannot answer for Ravi
        await capabilities.answer(token, "yes", member="ravi")
    assert e.value.code == capm.CAPABILITY_SCOPE

    out = await capabilities.answer(token, "yes", "<script>alert(1)</script> fine by me")
    assert out["recorded"] == "yes"
    consents = recovery.vote_status(voting, recovery.latest_set(voting).id)["consents"]
    assert "tomas" in consents["given"]
    from app.db import session_scope

    with session_scope() as db:
        use = db.get(
            CapabilityUse, capm.verify(token, secret=get_settings().mandate_hmac_secret, now=0).nonce
        )
        assert use is not None and "<script>" not in use.rationale and "&lt;script&gt;" in use.rationale

    with pytest.raises(capm.CapabilityError) as e:  # second use (the DB row, so also after a restart)
        await capabilities.answer(token, "yes")
    assert e.value.code == capm.CAPABILITY_USED


async def test_outside_yes_the_mandate_refuses_is_recorded_as_no(client, voting, monkeypatch):
    token = _mint(client, voting, "tomas")

    class Refuse:
        choice, reason, rule = "no", "swap moves your payout later than your rule allows", "swap_refused"

    monkeypatch.setattr(member_agent, "decide", lambda *a, **k: Refuse())
    out = await capabilities.answer(token, "yes")
    assert out["asked"] == "yes" and out["recorded"] == "no" and "recorded as no" in out["note"]


async def test_expired_and_superseded(client, voting, monkeypatch):
    token = _mint(client, voting, "tomas")
    monkeypatch.setattr(capabilities, "_now", lambda: 10**10)
    with pytest.raises(capm.CapabilityError) as e:
        await capabilities.answer(token, "yes")
    assert e.value.code == capm.CAPABILITY_EXPIRED
    monkeypatch.undo()

    token = _mint(client, voting, "tomas")
    from app.db import session_scope
    from app.models import RemedySetRow

    with session_scope() as db:  # the proposal is edited after the token was issued
        row = db.get(RemedySetRow, recovery.latest_set(voting).id)
        row.json = row.json.replace('"round_n"', '"round_n" ', 1)
        db.add(row)
    with pytest.raises(capm.CapabilityError) as e:
        await capabilities.answer(token, "yes")
    assert e.value.code == capm.SET_SUPERSEDED


async def test_read_token_cannot_answer_and_non_demo_circle_is_refused(client, voting):
    token = _mint(client, voting, "ravi", scope="read")
    with pytest.raises(capm.CapabilityError) as e:
        await capabilities.answer(token, "yes")
    assert e.value.code == capm.CAPABILITY_SCOPE
    circle = state.load_circle(voting)
    from app.db import session_scope
    from app.models import Circle

    with session_scope() as db:
        row = db.get(Circle, circle.id)
        row.is_fixture_clone = False
        db.add(row)
    with pytest.raises(capm.CapabilityError) as e:
        capabilities.mint_answer(voting, "tomas")
    assert e.value.code == capm.NOT_A_DEMO_CIRCLE


async def test_public_mcp_tools_return_refusals_not_exceptions(client, voting):
    from fastmcp import Client

    from app.mcp.public import paypal_ops_public

    token = _mint(client, voting, "tomas")
    async with Client(paypal_ops_public) as mcp:
        got = (await mcp.call_tool("get_request", {"cap": token, "member": ""})).structured_content
        assert got["ok"] is True and got["request"]["item"] == "consent"
        refused = (
            await mcp.call_tool(
                "answer_request", {"cap": token, "choice": "yes", "rationale": "", "member": "ravi"}
            )
        ).structured_content
        assert refused == {"ok": False, "error": "CAPABILITY_SCOPE", "message": refused["message"]}
        assert "for tomas only" in refused["message"]
        done = (
            await mcp.call_tool(
                "answer_request", {"cap": token, "choice": "yes", "rationale": "ok", "member": ""}
            )
        ).structured_content
        assert done["recorded"] == "yes"
        again = (
            await mcp.call_tool(
                "answer_request", {"cap": token, "choice": "yes", "rationale": "", "member": ""}
            )
        ).structured_content
        assert again["error"] == "CAPABILITY_USED"
        bad = (await mcp.call_tool("get_my_repayment", {"cap": "tkc1.x.y", "member": ""})).structured_content
        assert bad["error"] == "CAPABILITY_INVALID"


def test_minting_mid_story_taints_the_recording(client, voting):
    _mint(client, voting, "tomas")
    assert story.load(voting).tainted
