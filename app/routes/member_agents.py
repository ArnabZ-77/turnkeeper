"""Member mandates and member agents (flag ``member_mandates``, default OFF; kill switch TK_MEMBER_AGENTS=0).

GET  /api/circles/{circle_id}/mandates              -> each member's signed mandate, signature check, honest labels
POST /api/circles/{circle_id}/member-agents/run     -> every pending member agent answers inside its mandate
                                                       {set_id?, answers?: {member: "yes"|"no"}} (humans' taps for asks)

Both answer 404 {"detail": ...} when the feature is off for the circle (flag off, or the kill switch). Nothing
here reaches PayPal.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.engine import member_agent, member_mandates, recovery, sessions, tasks
from app.engine.events import get_event_bus
from app.engine.sessions import require_session
from app.models import Session

CurrentSession = Annotated[Session, Depends(require_session)]

log = logging.getLogger("turnkeeper.routes.member_agents")

router = APIRouter(tags=["member-agents"])

OFF_DETAIL = "member mandates are off for this circle"


class RunBody(BaseModel):
    set_id: str | None = Field(default=None, pattern=r"^rs_[0-9a-f]{8}$")
    answers: dict[str, str] = Field(default_factory=dict)  # member handle -> "yes" | "no" (a human's tap)


def require_enabled(circle_id: str) -> None:
    """404 unless the feature is on for the circle (flag or TK_MEMBER_MANDATES) and not killed."""
    if member_mandates.killed():
        raise HTTPException(status_code=404, detail="member agents are switched off (TK_MEMBER_AGENTS=0)")
    if not member_mandates.enabled(circle_id):
        raise HTTPException(status_code=404, detail=OFF_DETAIL)


@router.get("/api/circles/{circle_id}/mandates")
async def get_mandates(circle_id: str, sess: CurrentSession) -> dict[str, Any]:
    sessions.owns_circle(sess, circle_id)
    require_enabled(circle_id)
    return member_mandates.mandates_view(circle_id)


@router.post("/api/circles/{circle_id}/member-agents/run")
async def run_agents(
    circle_id: str, sess: CurrentSession, request: Request, body: RunBody | None = None
) -> dict[str, Any]:
    sessions.owns_circle(sess, circle_id)
    require_enabled(circle_id)
    body = body or RunBody()
    bad = [k for k, v in body.answers.items() if v not in ("yes", "no")]
    if bad:
        raise HTTPException(status_code=422, detail=f"answers must be yes or no (got {', '.join(bad)})")
    from app.engine import story

    story.taint(circle_id, "member_agents_run")  # a direct run mid-story: that run is not the canonical replay
    try:
        res = await member_agent.run_member_agents(
            circle_id,
            body.set_id,
            answers=body.answers,
            answer_source="member",
            publish=get_event_bus().publisher(sess.sid),
            live_allowed=tasks.live_allowed_for(request),
        )
    except recovery.RecoveryError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"action": "member_agents_run", **res}
