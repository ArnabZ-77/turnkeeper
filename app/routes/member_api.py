"""Member capability API: the public MCP's member tools over plain HTTP, for a member's own assistant.

GET  /api/member/request     -> what the member is asked            (MCP get_request)
POST /api/member/answer      -> {choice, rationale?, member?}       (MCP answer_request: single use, mandate floor)
GET  /api/member/repayment   -> the member's repayment invoice      (MCP get_my_repayment)

Authenticated by the member capability alone, in the ``X-Turnkeeper-Capability`` header (never a URL): the same
HMAC token, scope, expiry, single-use nonce and ``member_agent.decide`` floor as the MCP tools
(``app.engine.capabilities``), so an assistant that has no MCP client, or no operator bearer token, can still act
for exactly one member and one question. Named refusals come back as 403 ``{"error": <CODE>, "message": ...}``.
No endpoint here moves money. 30 requests a minute per IP.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.domain.capability import CapabilityError
from app.engine import capabilities
from app.engine.sessions import circle_publisher
from app.mcp.auth import RateLimiter, client_ip

router = APIRouter(prefix="/api/member", tags=["member"])

HEADER = "X-Turnkeeper-Capability"
MEMBER_LIMITER = RateLimiter(limit=30, window=60.0)


class AnswerBody(BaseModel):
    choice: str = Field(pattern=r"^(yes|no)$")
    rationale: str = Field(default="", max_length=2000)
    member: str | None = Field(default=None, max_length=40)


def _guard(request: Request, token: str | None) -> str:
    allowed, retry_after = MEMBER_LIMITER.check(f"member|{client_ip(request.scope)}")
    if not allowed:
        raise HTTPException(
            status_code=429, detail="rate limited", headers={"Retry-After": str(int(retry_after) + 1)}
        )
    if not token:
        raise HTTPException(status_code=401, detail=f"missing {HEADER}")
    return token


def _refused(e: CapabilityError) -> HTTPException:
    return HTTPException(status_code=403, detail=e.as_dict())


@router.get("/request")
async def get_request(
    request: Request, cap: str | None = Header(default=None, alias=HEADER)
) -> dict[str, Any]:
    token = _guard(request, cap)
    try:
        return capabilities.request_view(token)
    except CapabilityError as e:
        raise _refused(e) from None


@router.post("/answer")
async def post_answer(
    body: AnswerBody, request: Request, cap: str | None = Header(default=None, alias=HEADER)
) -> dict[str, Any]:
    token = _guard(request, cap)
    try:
        claims = capabilities.open_token(token, body.member)
        return await capabilities.answer(
            token, body.choice, body.rationale, member=body.member, publish=circle_publisher(claims.circle_id)
        )
    except CapabilityError as e:
        raise _refused(e) from None


@router.get("/repayment")
async def get_repayment(
    request: Request, cap: str | None = Header(default=None, alias=HEADER)
) -> dict[str, Any]:
    token = _guard(request, cap)
    try:
        return await capabilities.repayment(token)
    except CapabilityError as e:
        raise _refused(e) from None
