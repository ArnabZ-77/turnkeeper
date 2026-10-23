"""M3 end to end: the whole Judge Mode story through POST /api/judge/story/next, plus compile -> freeze -> join.

The sandbox is mocked with respx (tests/integration/conftest.py), the Agent Toolkit's invoice tools are faked at
`ToolkitAdapter.run` (FakeToolkit from the M2 test) and no LLM is configured (rules mode everywhere: forecast
templates, rules treasurer, rules charter parser). Members are given test vault ids after the story's reset, so
the story takes its live-sandbox path (real charges through respx, a real payout for rounds 3 and 4).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from integration.conftest import (
    POT,
    SandboxMock,
    board_of,
    contributions,
    recent_events,
    round_row,
    start_session,
    vault_members,
)
from integration.test_m2_recovery import (  # noqa: F401 - the toolkit fixture is used by name
    FAKE_INVOICE_ID,
    OTHERS,
    ROTATION_AFTER,
    ROTATION_BEFORE,
    FakeToolkit,
    toolkit,
)

STORY = [
    "reset",
    "advance_to_t3",
    "run_round3",
    "reply_as_ravi",
    "start_recovery",
    "consents",
    "votes",
    "approve",
    "execute",
    "payout",
    "clean_round4",
]


def story(client: TestClient) -> dict:
    r = client.get("/api/judge/story")
    assert r.status_code == 200, r.text
    return r.json()


def next_step(client: TestClient, expected: str, timeout: float = 20.0) -> dict:
    """Run the story's next step; wait for a background step to settle (it counts only once its outcome holds)."""
    view = story(client)
    assert view["next"]["step"] == expected, view["next"]
    assert view["next"]["action_label"].startswith("Next: ")
    r = client.post("/api/judge/story/next")
    assert r.status_code == 200, (expected, r.text)
    out = r.json()
    assert out["step"] == expected
    if out.get("task_id"):
        deadline = time.monotonic() + timeout
        while True:
            view = story(client)
            done = [s["step"] for s in view["steps"] if s["status"] == "done"]
            if expected in done:
                break
            assert view["running"] or expected in done, f"step {expected} did not settle: {view}"
            if time.monotonic() > deadline:
                raise AssertionError(f"story step {expected} did not finish: {view}")
            time.sleep(0.1)
    return out


def test_judge_mode_story_end_to_end(client: TestClient, sandbox: SandboxMock, toolkit: FakeToolkit):  # noqa: F811
    first = start_session(client)
    view = story(client)
    assert view["next"]["step"] == "reset" and view["complete"] is False

    # reset: a new circle and run id; give the new members test vault ids so the live-sandbox path runs
    out = next_step(client, "reset")
    sess = out["session"]
    cid, sid = sess["circle_id"], sess["sid"]
    assert cid != first["circle_id"] and sess["run_id"] != first["run_id"]
    vault_members(cid)
    view = story(client)
    assert view["paypal"] == "live" and view["current"] == "reset"
    assert view["next"]["step"] == "advance_to_t3"
    board = board_of(client, cid)
    assert board["circle"]["rotation"] == ROTATION_BEFORE
    assert contributions(board, 3)["ravi"]["predicted"] is False  # no badge before the forecast step

    # T-3: the forecast flags ravi (rules templates; no LLM configured)
    out = next_step(client, "advance_to_t3")
    assert out["result"]["flagged"] == ["ravi"], out["result"]
    assert out["mode"] == "rules"
    board = board_of(client, cid)
    flagged = {f["member"] for f in board["forecasts"] if f.get("flagged")}
    assert flagged == {"ravi"}, board["forecasts"]
    assert contributions(board, 3)["ravi"]["predicted"] is True

    # Round 3: ravi DECLINED, the five others captured; the pot is held
    next_step(client, "run_round3")
    board = board_of(client, cid)
    rows = contributions(board, 3)
    assert rows["ravi"]["status"] == "DECLINED"
    assert all(rows[m]["status"] == "COMPLETED" for m in OTHERS)
    assert round_row(board, 3)["status"] != "PAID"
    assert sandbox.payouts == []

    out = next_step(client, "reply_as_ravi")
    assert out["result"]["payday"]
    next_step(client, "start_recovery")
    rec = client.get(f"/api/circles/{cid}/recovery").json()
    assert rec["status"] == "voting" and rec["source"] == "rules"
    assert {r["type"] for r in rec["remedies"]} == {"buffer_advance", "repay_by_invoice", "turn_swap"}

    next_step(client, "consents")
    out = next_step(client, "votes")
    assert out["result"]["votes"]["passed"] is True
    next_step(client, "approve")

    # execute: ravi COVERED, rotation changed, invoice created through the (fake) toolkit; payout not yet
    next_step(client, "execute")
    board = board_of(client, cid)
    assert contributions(board, 3)["ravi"]["status"] == "COVERED"
    assert board["circle"]["rotation"] == ROTATION_AFTER
    assert board["recovery"]["invoice"]["id"] == FAKE_INVOICE_ID
    assert toolkit.methods() == ["create_invoice", "send_invoice", "generate_invoice_qr_code"]

    # payout: round 3 PAID to dev with the full pot
    next_step(client, "payout")
    board = board_of(client, cid)
    r3 = round_row(board, 3)
    assert r3["status"] == "PAID" and r3["payout"]["amount"] == str(POT), r3
    assert len(sandbox.payouts) == 1 and sandbox.payouts[0]["item"]["receiver"] == "dev@example.com"
    approvals_before = len(recent_events(sid, "approval"))

    # clean round 4: every charge captured and the pot paid with zero organizer clicks
    next_step(client, "clean_round4")
    board = board_of(client, cid)
    rows = contributions(board, 4)
    assert rows and all(c["status"] == "COMPLETED" for c in rows.values()), rows
    r4 = round_row(board, 4)
    assert r4["status"] == "PAID", r4
    assert r4["recipient"] == ROTATION_AFTER[3]
    assert len(sandbox.payouts) == 2
    assert sandbox.payouts[1]["item"]["receiver"] == f"{ROTATION_AFTER[3]}@example.com"
    assert len(recent_events(sid, "approval")) == approvals_before

    view = story(client)
    assert view["complete"] is True and view["next"] is None
    assert [s["step"] for s in view["steps"]] == STORY
    assert all(s["status"] == "done" for s in view["steps"])
    assert {s["mode"] for s in view["steps"]} <= {"judge", "rules", "live"}

    # the completed run became the canonical replay (no session needed)
    replay = TestClient(client.app).get("/api/replay")
    assert replay.status_code == 200, replay.text
    rec = replay.json()
    assert rec["source"] == "recorded" and rec["paypal_mode"] == "live"
    assert [s["step"] for s in rec["steps"]] == STORY
    assert "@example.com" not in replay.text  # emails scrubbed


# --- compile -> freeze -> join start -> return ---------------------------------------------------------


@pytest.fixture
def vault_api(sandbox: SandboxMock) -> dict[str, Any]:
    calls: dict[str, Any] = {"setup": [], "payment": []}

    def setup(request: httpx.Request) -> httpx.Response:
        calls["setup"].append(request)
        n = len(calls["setup"])
        return httpx.Response(
            200,
            json={
                "id": f"SETUPTOK{n:03d}",
                "status": "PAYER_ACTION_REQUIRED",
                "links": [
                    {
                        "rel": "approve",
                        "href": f"https://www.sandbox.paypal.com/agreements/approve?approval_session_id=S{n}",
                        "method": "GET",
                    }
                ],
            },
        )

    def get_setup(request: httpx.Request, token_id: str) -> httpx.Response:
        return httpx.Response(200, json={"id": token_id, "status": "APPROVED"})

    def payment(request: httpx.Request) -> httpx.Response:
        calls["payment"].append(request)
        return httpx.Response(
            200,
            json={
                "id": "TESTVAULTJOINED",
                "customer": {"id": "TESTCUSTJOINED"},
                "payment_source": {"paypal": {"email_address": "joiner@example.com"}},
            },
        )

    sandbox.router.post("/v3/vault/setup-tokens").mock(side_effect=setup)
    sandbox.router.get(path__regex=r"^/v3/vault/setup-tokens/(?P<token_id>[^/]+)$").mock(
        side_effect=get_setup
    )
    sandbox.router.post("/v3/vault/payment-tokens").mock(side_effect=payment)
    return calls


def test_compile_freeze_join_flow(client: TestClient, vault_api: dict[str, Any]):
    sess = start_session(client)
    demo = client.get("/api/circles/compile/demo?lang=hi").json()
    assert demo["lang"] == "hi" and demo["text"]

    # the demo text compiles (rules parser, labelled) to exactly the demo circle's charter
    r = client.post("/api/circles/compile", json={"text": demo["text"], "lang": "hi"})
    assert r.status_code == 200, r.text
    comp = r.json()
    assert comp["mode_label"].startswith("rules mode")
    assert comp["matches_circle"] is True

    # an edited charter (different amount) is a new circle once Priya confirms
    edited = demo["text"].replace("200", "150") if "200" in demo["text"] else None
    assert edited, "demo text carries the contribution amount"
    comp2 = client.post("/api/circles/compile", json={"text": edited, "lang": "hi"}).json()
    assert comp2["ok"] is True, comp2.get("problems")
    assert comp2["matches_circle"] is False

    cid = sess["circle_id"]
    prev = client.post(f"/api/circles/{cid}/charter/freeze", json={"draft_id": comp2["draft_id"]})
    assert prev.status_code == 200, prev.text
    prev = prev.json()
    assert prev["confirmed"] is False and prev["charter_sha256"] == comp2["charter_sha256"]
    assert prev["billing_plan"]

    conf = client.post(
        f"/api/circles/{cid}/charter/freeze", json={"draft_id": comp2["draft_id"], "confirm": True}
    )
    assert conf.status_code == 200, conf.text
    conf = conf.json()
    assert conf["confirmed"] is True and conf["created_circle"] is True and conf["circle_id"] != cid
    assert conf["invites"]
    assert client.get("/api/session").json()["circle_id"] == cid  # the session keeps its demo board

    inv = conf["invites"][0]
    token = inv["token"]
    assert inv["vaulted"] is False and token in inv["url"]

    card = client.get(f"/api/join/{token}")
    assert card.status_code == 200, card.text
    card = card.json()
    assert card["circle"]["amount"] == "150.00" and card["vaulted"] is False
    assert card["circle"]["billing_plan"] and card["circle"]["fee_note"]

    start = client.post(f"/api/join/{token}/start")
    assert start.status_code == 200, start.text
    st = start.json()
    assert st["setup_token_id"] == "SETUPTOK001" and st["approve_url"].startswith(
        "https://www.sandbox.paypal.com/"
    )
    body = vault_api["setup"][0]
    assert body.headers.get("PayPal-Request-Id")

    ret = client.get(
        f"/join/{token}/return", params={"approval_token_id": "SETUPTOK001"}, follow_redirects=False
    )
    assert ret.status_code == 303, ret.text
    loc = urlparse(ret.headers["location"])
    assert loc.path.startswith("/join/") and parse_qs(loc.query)["status"] == ["vaulted"]
    assert len(vault_api["payment"]) == 1
    fresh = loc.path.removeprefix("/join/")
    assert fresh != token  # the invite nonce rotated on VAULTED: the pre-join link is revoked
    assert client.get(f"/api/join/{token}").status_code == 404

    card = client.get(f"/api/join/{fresh}").json()
    assert card["vaulted"] is True, card

    # the SPA serves the redirect target (the member's page renders the status)
    page = client.get(f"/join/{fresh}?status=vaulted")
    assert page.status_code == 200
