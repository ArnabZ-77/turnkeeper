"""Member capability API over HTTP (/api/member/*): same rules as the public MCP member tools."""

from __future__ import annotations

from routes.test_capabilities import _mint, isolated, toolkit, voting  # noqa: F401, F811 - pytest fixtures

H = "X-Turnkeeper-Capability"


def test_needs_the_capability_header(client):
    assert client.get("/api/member/request").status_code == 401
    r = client.get("/api/member/request", headers={H: "tkc1.not.valid"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "CAPABILITY_INVALID"


def test_request_answer_once_and_scope(client, voting):  # noqa: F811
    token = _mint(client, voting, "tomas")
    got = client.get("/api/member/request", headers={H: token}).json()
    assert got["request"]["item"] == "consent" and got["request"]["choices"] == ["yes", "no"]

    other = client.post("/api/member/answer", headers={H: token}, json={"choice": "yes", "member": "ravi"})
    assert other.status_code == 403 and other.json()["detail"]["error"] == "CAPABILITY_SCOPE"

    ok = client.post(
        "/api/member/answer", headers={H: token}, json={"choice": "yes", "rationale": "fine by me"}
    )
    assert ok.status_code == 200 and ok.json()["recorded"] == "yes"

    again = client.post("/api/member/answer", headers={H: token}, json={"choice": "yes"})
    assert again.status_code == 403 and again.json()["detail"]["error"] == "CAPABILITY_USED"


def test_bad_choice_is_422_and_repayment_reads(client, voting):  # noqa: F811
    token = _mint(client, voting, "ravi", scope="read")
    assert client.post("/api/member/answer", headers={H: token}, json={"choice": "maybe"}).status_code == 422
    r = client.get("/api/member/repayment", headers={H: token})
    assert r.status_code == 200 and r.json()["member"] == "ravi"


def test_rate_limited(client, monkeypatch):
    from app.routes import member_api

    monkeypatch.setattr(member_api, "MEMBER_LIMITER", member_api.RateLimiter(limit=2, window=60.0))
    codes = [client.get("/api/member/request", headers={H: "tkc1.x.y"}).status_code for _ in range(3)]
    assert codes == [403, 403, 429]


# --- same bytes as the public MCP: mask_pii(jsonable(...)), first names, a controlled 500 -------------------------


async def _mcp(tool: str, args: dict) -> dict:
    from fastmcp import Client

    from app.mcp.public import paypal_ops_public

    async with Client(paypal_ops_public) as mcp:
        return (await mcp.call_tool(tool, args)).structured_content


async def test_http_and_mcp_answer_the_same_for_the_same_token(client, voting):  # noqa: F811
    from app.engine import recovery

    short = recovery.domain_set(recovery.latest_set(voting)).member
    token = _mint(client, voting, short, scope="read")  # a read token can be used again and again
    http = client.get("/api/member/request", headers={H: token})
    assert http.status_code == 200
    assert http.json() == await _mcp("get_request", {"cap": token, "member": ""})
    pay = client.get("/api/member/repayment", headers={H: token})
    assert pay.status_code == 200
    assert pay.json() == await _mcp("get_my_repayment", {"cap": token, "member": ""})


async def test_the_short_member_is_named_by_first_name_only(client, voting):  # noqa: F811
    from app.engine import recovery, state

    short = recovery.domain_set(recovery.latest_set(voting)).member
    full = next(m.name for m in state.load_charter(voting).members if m.handle == short)
    assert " " in full  # the charter has the full name ...
    token = _mint(client, voting, "tomas")  # an answer token is named: it sees who it decides about
    http = client.get("/api/member/request", headers={H: token}).json()
    mcp = await _mcp("get_request", {"cap": token, "member": ""})
    assert (
        http["request"]["short_member"] == mcp["request"]["short_member"] == full.split()[0]
    )  # ... never shown
    assert full not in str(http) and full not in str(mcp)


def test_http_success_is_masked_like_the_mcp(client, monkeypatch):
    import datetime as dt
    import decimal

    from app.engine import capabilities

    monkeypatch.setattr(
        capabilities,
        "request_view",
        lambda token, member=None: {
            "ok": True,
            "name": "Ravi Sharma",
            "email": "ravi@example.com",
            "note": "write to ravi@example.com or call +1 555 010 1234 by 2027-01-08",
            "amount": decimal.Decimal("200"),
            "due_date": dt.date(2027, 1, 8),
        },
    )
    r = client.get("/api/member/request", headers={H: "tkc1.x.y"})
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "name": "Ravi",
        "note": "write to [email] or call [phone] by 2027-01-08",
        "amount": "200.00",
        "due_date": "2027-01-08",
    }


def test_an_unexpected_failure_is_a_controlled_500(client, monkeypatch):
    from app.engine import capabilities
    from app.engine.common import GENERIC_ERROR_TEXT

    def boom(token, member=None):
        raise RuntimeError("database at postgres://admin:hunter2@db exploded")

    monkeypatch.setattr(capabilities, "request_view", boom)
    r = client.get("/api/member/request", headers={H: "tkc1.x.y"})
    assert r.status_code == 500
    assert r.json() == {"detail": {"ok": False, "error": "RuntimeError", "message": GENERIC_ERROR_TEXT}}
    assert "hunter2" not in r.text
