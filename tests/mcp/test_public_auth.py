"""Public MCP server (PII masking, demo-only) and the bearer/rate-limit middleware + mount."""

import json

import fastmcp
import pytest
from engine_fakes import DEMO_CIRCLE, OTHER_CIRCLE
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.mcp import auth as auth_mod
from app.mcp.auth import RateLimiter, mount_public_mcp
from app.mcp.public import mask_pii, paypal_ops_public

RPC_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _rpc_call(name: str, args: dict, id_: int = 2) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": {"name": name, "arguments": args}}


async def public_call(name: str, **args):
    async with fastmcp.Client(paypal_ops_public) as c:
        result = await c.call_tool(name, args, raise_on_error=False)
    assert result.is_error is False
    return result.data


# ---------------------------------------------------------------------------------------------- masking


def test_mask_pii_drops_contacts_and_reduces_names():
    data = {
        "members": [
            {"handle": "asha", "name": "Asha Patel", "email": "asha@example.com", "phone": "+1 555 010 1234"}
        ],
        "note": "call Ravi at +15550101234 or ravi.k@example.org",
        "payout": {"receiver": "x@y.com", "amount": "200.00", "vault_id": "vid"},
        "nested": [{"name": "Lin Chen", "customer_id": "c1"}],
    }
    out = mask_pii(data)
    assert out["members"] == [{"handle": "asha", "name": "Asha"}]
    assert out["note"] == "call Ravi at [phone] or [email]"
    assert out["payout"] == {"amount": "200.00"}
    assert out["nested"] == [{"name": "Lin"}]
    assert "@" not in json.dumps(out)


async def test_get_circle_is_masked_and_demo_only(fake_engine):
    data = await public_call("get_circle", circle_id=DEMO_CIRCLE)
    assert data["ok"] is True
    for m in data["members"]:
        assert " " not in m["name"] and "email" not in m and "sandbox_account" not in m
    assert "@" not in json.dumps(data)
    denied = await public_call("get_circle", circle_id=OTHER_CIRCLE)
    assert denied == {
        "ok": False,
        "error": "NOT_A_DEMO_CIRCLE",
        "message": f"circle {OTHER_CIRCLE!r} is not a demo circle",
    }
    assert fake_engine.calls_named("board_model") == [{"circle_id": DEMO_CIRCLE}]


async def test_get_charter_masks_members_and_keeps_rules(fake_engine):
    data = await public_call("get_charter", circle_id=DEMO_CIRCLE)
    assert data["ok"] is True
    assert data["amount"] == "200.00" and data["rotation"][0] == "meena" and data["quorum"] == "4/6"
    ravi = next(m for m in data["members"] if m["handle"] == "ravi")
    assert ravi["name"] == "Ravi" and "email" not in ravi and "payout_receiver" not in ravi
    assert ravi["payday_rule"] == "every 2nd Friday"


async def test_round_matrix_and_payout_status_public(fake_engine):
    matrix = await public_call("get_round_matrix", circle_id=DEMO_CIRCLE)
    assert matrix["ok"] is True and len(matrix["contributions"]) == 12
    assert all(" " not in m["name"] for m in matrix["members"])
    status = await public_call("get_payout_status", circle_id=DEMO_CIRCLE, round_n=1)
    assert status == {"ok": True, "status": None, "item_id": None, "batch_id": None}
    assert (await public_call("get_payout_status", circle_id=OTHER_CIRCLE, round_n=1))["ok"] is False


async def test_is_demo_circle_fails_closed_without_engine(monkeypatch):
    import sys

    from app.mcp import public

    for name in ("app.engine.state", "app.db", "app.models"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(
        public.internal, "_state", lambda: (_ for _ in ()).throw(ModuleNotFoundError("app.engine"))
    )
    monkeypatch.setitem(sys.modules, "app.db", None)  # import_module raises ImportError for None entries
    assert await public.is_demo_circle(DEMO_CIRCLE) is False


# ---------------------------------------------------------------------------------------------- limiter


def test_rate_limiter_sliding_window():
    now = [1000.0]
    limiter = RateLimiter(limit=3, window=60.0, clock=lambda: now[0])
    assert [limiter.check("k")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.check("k")
    assert allowed is False and 0 < retry_after <= 60
    assert limiter.check("other")[0] is True  # separate key
    now[0] += 61
    assert limiter.check("k")[0] is True


# ------------------------------------------------------------------------------------------ middleware


def _app_with_public_mcp() -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    mount_public_mcp(app)

    @app.get("/{full_path:path}")  # mimic the SPA catch-all that app/main.py adds after the routers
    async def spa(full_path: str):
        return {"spa": full_path}

    return app


def test_mount_is_idempotent_and_serves_exactly_mcp(demo_token):
    app = _app_with_public_mcp()
    first = app.state.public_mcp_app
    assert mount_public_mcp(app) is first
    assert [r.path for r in app.router.routes].count("/mcp") == 1


def test_401_without_or_with_wrong_token(demo_token):
    with TestClient(_app_with_public_mcp()) as c:
        r = c.post("/mcp", json=TOOLS_LIST, headers=RPC_HEADERS)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
        r = c.post("/mcp", json=TOOLS_LIST, headers={**RPC_HEADERS, "Authorization": "Bearer nope"})
        assert r.status_code == 401
        r = c.post("/mcp", json=TOOLS_LIST, headers={**RPC_HEADERS, "Authorization": "Basic abc"})
        assert r.status_code == 401
        # other routes are untouched by the middleware
        assert c.get("/healthz").status_code == 200
        assert c.get("/index.html").json() == {"spa": "index.html"}


def test_503_when_token_not_configured(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MCP_DEMO_TOKEN", "")
    get_settings.cache_clear()
    try:
        with TestClient(_app_with_public_mcp()) as c:
            r = c.post("/mcp", json=TOOLS_LIST, headers={**RPC_HEADERS, "Authorization": "Bearer anything"})
            assert r.status_code == 503
            assert r.json()["error"] == "MCP demo token not configured"
    finally:
        get_settings.cache_clear()


def test_valid_token_lists_and_calls_read_only_tools_with_masking(demo_token, fake_engine):
    headers = {**RPC_HEADERS, "Authorization": f"Bearer {demo_token}"}
    with TestClient(_app_with_public_mcp()) as c:
        r = c.post("/mcp", json=TOOLS_LIST, headers=headers)
        assert r.status_code == 200, r.text
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert names == {"get_circle", "get_round_matrix", "get_charter", "get_payout_status"}
        r = c.post("/mcp", json=_rpc_call("get_circle", {"circle_id": DEMO_CIRCLE}), headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()["result"]
        assert body["isError"] is False
        board = body["structuredContent"]
        assert board["ok"] is True
        assert [m["name"] for m in board["members"]] == ["Asha", "Ravi", "Meena", "Tomas", "Lin", "Dev"]
        assert "@" not in r.text
        r = c.post("/mcp", json=_rpc_call("get_circle", {"circle_id": OTHER_CIRCLE}, 3), headers=headers)
        assert r.json()["result"]["structuredContent"]["error"] == "NOT_A_DEMO_CIRCLE"


def test_rate_limit_30_per_minute_per_token_and_ip(demo_token):
    headers = {**RPC_HEADERS, "Authorization": f"Bearer {demo_token}"}
    with TestClient(_app_with_public_mcp()) as c:
        codes = [c.post("/mcp", json=TOOLS_LIST, headers=headers).status_code for _ in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429
        r = c.post("/mcp", json=TOOLS_LIST, headers=headers)
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        # another IP (forwarded by Render's proxy) has its own budget
        r = c.post("/mcp", json=TOOLS_LIST, headers={**headers, "X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
        assert r.status_code == 200


def test_client_ip_prefers_forwarded_for():
    assert (
        auth_mod.client_ip(
            {"headers": [(b"x-forwarded-for", b"198.51.100.4, 10.1.1.1")], "client": ("127.0.0.1", 1)}
        )
        == "198.51.100.4"
    )
    assert auth_mod.client_ip({"headers": [], "client": ("127.0.0.1", 1)}) == "127.0.0.1"
    assert auth_mod.client_ip({"headers": []}) == "unknown"


@pytest.mark.parametrize("token_header", ["Bearer test-demo-token", "bearer test-demo-token"])
def test_bearer_scheme_is_case_insensitive(demo_token, token_header):
    with TestClient(_app_with_public_mcp()) as c:
        r = c.post("/mcp", json=TOOLS_LIST, headers={**RPC_HEADERS, "Authorization": token_header})
        assert r.status_code == 200
