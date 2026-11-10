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
