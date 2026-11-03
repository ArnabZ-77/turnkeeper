"""Member Passbook API (flag TK_PASSBOOK, default off: 404).

GET /api/passbook       -> one member's view (app.engine.passbook), capability in the X-Turnkeeper-Capability header
GET /api/passbook/qr    -> that member's own repayment QR code (PNG)
POST /api/passbook/links/{circle_id} -> the organizer's per-member links (session owner only)

The page is /m#<capability>: the token stays in the URL fragment, which browsers never send to a server, so it is
never in Turnkeeper's own access logs or a Referer header. (A link sent through a messenger is, of course, seen by
that messenger.)
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response

from app.domain.capability import CapabilityError
from app.engine import capabilities, passbook, sessions, state
from app.engine.sessions import require_session
from app.models import Session

CurrentSession = Annotated[Session, Depends(require_session)]

router = APIRouter(prefix="/api/passbook", tags=["passbook"])

HEADER = "X-Turnkeeper-Capability"


def _token(value: str | None) -> str:
    if not passbook.enabled():
        raise HTTPException(status_code=404, detail="the member passbook is off (TK_PASSBOOK)")
    if not value:
        raise HTTPException(status_code=401, detail=f"missing {HEADER}")
    return value


@router.get("")
async def get_passbook(cap: str | None = Header(default=None, alias=HEADER)) -> dict[str, Any]:
    token = _token(cap)
    try:
        return passbook.view(token)
    except CapabilityError as e:
        raise HTTPException(status_code=403, detail=e.as_dict()) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="circle not found") from None


@router.get("/qr")
async def get_passbook_qr(cap: str | None = Header(default=None, alias=HEADER)) -> Response:
    token = _token(cap)
    try:
        png = passbook.qr_png(token)
    except CapabilityError as e:
        raise HTTPException(status_code=403, detail=e.as_dict()) from None
    if png is None:
        raise HTTPException(status_code=404, detail="no repayment QR code for this member")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/links/{circle_id}")
async def passbook_links(circle_id: str, sess: CurrentSession) -> dict[str, Any]:
    """One read-only passbook link per member (a day), for the organizer to send. The token rides in the
    fragment: ``/m#<token>``."""
    if not passbook.enabled():
        raise HTTPException(status_code=404, detail="the member passbook is off (TK_PASSBOOK)")
    sessions.owns_circle(sess, circle_id)
    try:
        links = [
            {
                "member": m.handle,
                "name": m.name,
                "path": "/m#" + capabilities.mint_read(circle_id, m.handle)["token"],
            }
            for m in state.members_of(circle_id)
        ]
    except CapabilityError as e:
        raise HTTPException(status_code=409, detail=e.as_dict()) from None
    return {"ok": True, "links": links}
