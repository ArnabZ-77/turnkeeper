"""Judge Mode: POST /api/judge/{action} with {circle_id, ...}.

Actions: reset, advance_clock {days}, run_round {round_n}, toggle_ravi_declines {on},
make_next_payout_unclaimed {on}. Later: reply_as_ravi, vote, approve.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.engine import sessions, state
from app.engine.clock import advance_clock
from app.engine.common import iso
from app.engine.events import get_event_bus
from app.engine.sessions import require_session
from app.models import Session
from app.routes.circles import session_shape, start_round_task

CurrentSession = Annotated[Session, Depends(require_session)]

log = logging.getLogger("turnkeeper.routes.judge")

router = APIRouter(prefix="/api/judge", tags=["judge"])

ACTIONS = ("reset", "advance_clock", "run_round", "toggle_ravi_declines", "make_next_payout_unclaimed")


class JudgeBody(BaseModel):
    circle_id: str
    days: float | None = Field(default=None, ge=0, le=366)
    round_n: int | None = Field(default=None, ge=1)
    on: bool | None = None


@router.post("/{action}")
async def judge_action(
    action: str, body: JudgeBody, sess: CurrentSession, request: Request
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise HTTPException(status_code=404, detail=f"unknown judge action {action!r}")
    bus = get_event_bus()

    if action == "reset":
        new = sessions.reset_session(sess.sid)
        await bus.publish(
            sess.sid, {"type": "toast", "level": "info", "text": "Session reset: new circle, new run id"}
        )
        await bus.publish(sess.sid, {"type": "board"})
        return {"ok": True, "action": action, "session": session_shape(new)}

    sessions.owns_circle(sess, body.circle_id)

    if action == "advance_clock":
        days = body.days if body.days is not None else 1.0
        new_now = advance_clock(body.circle_id, days)
        await bus.publish(
            sess.sid,
            {"type": "toast", "level": "info", "text": f"Clock advanced {days:g} day(s) to {iso(new_now)}"},
        )
        await bus.publish(sess.sid, {"type": "board"})
        return {"ok": True, "action": action, "virtual_now": iso(new_now)}

    if action == "run_round":
        if body.round_n is None:
            raise HTTPException(status_code=422, detail="round_n is required")
        started = start_round_task(
            "collect_round", body.circle_id, body.round_n, sess, fallback_engine=True, request=request
        )
        return {"ok": True, "action": action, **started}

    if action in ("toggle_ravi_declines", "make_next_payout_unclaimed"):
        flag = "ravi_declines" if action == "toggle_ravi_declines" else "next_payout_unclaimed"
        circle = state.load_circle(body.circle_id)
        current = state.get_flags(circle)[flag]
        value = (not current) if body.on is None else bool(body.on)
        flags = state.set_flag(body.circle_id, flag, value)
        await bus.publish(
            sess.sid, {"type": "toast", "level": "info", "text": f"{flag} = {'on' if value else 'off'}"}
        )
        await bus.publish(sess.sid, {"type": "board"})
        return {"ok": True, "action": action, "flags": flags}

    raise HTTPException(status_code=404, detail="unknown action")  # pragma: no cover
