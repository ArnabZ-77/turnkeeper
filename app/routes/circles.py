"""Session and circle routes.

GET  /api/session                                  -> {sid, run_id, circle_id, live_ai, paypal_live, frozen}
POST /api/session/reset                            -> same shape (new run_id, new clone)
GET  /api/circles/{circle_id}                      -> BoardModel
POST /api/circles/{circle_id}/rounds/{n}/collect   -> {"task_id"} (agent run in the background; 503 without B3)
POST /api/circles/{circle_id}/rounds/{n}/payout    -> {"task_id"}
GET  /api/tasks/{task_id}                          -> task status
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.engine import sessions, state, tasks
from app.engine.sessions import WaitlistFull, require_session
from app.models import Session

CurrentSession = Annotated[Session, Depends(require_session)]

log = logging.getLogger("turnkeeper.routes.circles")

router = APIRouter(prefix="/api", tags=["circles"])


def session_shape(sess: Session) -> dict[str, Any]:
    settings = get_settings()
    try:
        frozen = state.is_frozen(state.load_circle(sess.circle_id))
    except LookupError:
        frozen = bool(settings.freeze)
    return {
        "sid": sess.sid,
        "run_id": sess.run_id,
        "circle_id": sess.circle_id,
        "live_ai": settings.anthropic_configured,
        "paypal_live": settings.paypal_configured,
        "frozen": frozen,
    }


@router.get("/session")
async def get_session_route(request: Request, response: Response) -> Any:
    try:
        sess = sessions.get_or_create_session(request, response)
    except WaitlistFull as e:
        return JSONResponse(
            {"waitlist": True, "active": e.active, "max": e.maximum, "retry_after_s": 120},
            status_code=429,
            headers={"Retry-After": "120"},
        )
    return session_shape(sess)


@router.post("/session/reset")
async def reset_session_route(sess: CurrentSession) -> dict[str, Any]:
    new = sessions.reset_session(sess.sid)
    return session_shape(new)


@router.get("/circles/{circle_id}")
async def get_circle(circle_id: str) -> dict[str, Any]:
    try:
        return state.board_model(circle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="circle not found") from None


def start_round_task(
    kind: str, circle_id: str, n: int, sess: Session, *, fallback_engine: bool = False
) -> dict[str, Any]:
    """Shared by the collect/payout routes and Judge Mode's run_round."""
    sessions.owns_circle(sess, circle_id)
    if state.get_round(circle_id, n) is None:
        raise HTTPException(status_code=404, detail=f"round {n} not found")
    available, why = tasks.agent_available()
    if not available and not fallback_engine:
        raise HTTPException(
            status_code=503,
            detail=f"{why}. The treasurer agent (app.agent.treasurer) is not installed in this build.",
        )
    running = tasks.running_for(circle_id)
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail={"task_id": running.task_id, "message": "a run is already in progress for this circle"},
        )
    rec = tasks.start_task(
        kind, circle_id, n, sid=sess.sid, run_id=sess.run_id, fallback_engine=fallback_engine
    )
    log.info("task %s started kind=%s circle=%s round=%s", rec.task_id, kind, circle_id, n)
    return {"task_id": rec.task_id, "kind": kind, "circle_id": circle_id, "round_n": n, "agent": available}


@router.post("/circles/{circle_id}/rounds/{n}/collect", status_code=202)
async def collect_route(circle_id: str, n: int, sess: CurrentSession) -> dict[str, Any]:
    return start_round_task("collect_round", circle_id, n, sess)


@router.post("/circles/{circle_id}/rounds/{n}/payout", status_code=202)
async def payout_route(circle_id: str, n: int, sess: CurrentSession) -> dict[str, Any]:
    return start_round_task("payout", circle_id, n, sess)


@router.get("/tasks/{task_id}")
async def task_route(task_id: str) -> dict[str, Any]:
    status = tasks.task_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return status
