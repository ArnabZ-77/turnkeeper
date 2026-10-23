"""Session and circle routes.

GET  /api/session                                  -> {sid, run_id, circle_id, live_ai, paypal_live, frozen}
POST /api/session/reset                            -> same shape (new run_id, new clone)
GET  /api/circles/{circle_id}                      -> BoardModel (session-owned circle only, else 401/403)
POST /api/circles/{circle_id}/rounds/{n}/collect   -> {"task_id"} (agent run in the background; 503 without B3
                                                      or without PayPal sandbox credentials)
POST /api/circles/{circle_id}/rounds/{n}/payout    -> {"task_id"}
GET  /api/tasks/{task_id}                          -> task status (own session's tasks only, else 404)
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


def circle_vault_seeded(circle_id: str) -> bool:
    """Every member of the circle has a real (non-placeholder) sandbox vault id."""
    members = state.members_of(circle_id)
    return bool(members) and all(m.vault_id and not m.vault_id.startswith("VAULT_ID_") for m in members)


def session_shape(sess: Session) -> dict[str, Any]:
    settings = get_settings()
    try:
        frozen = state.is_frozen(state.load_circle(sess.circle_id))
    except LookupError:
        frozen = bool(settings.freeze)
    vault_seeded = circle_vault_seeded(sess.circle_id)
    return {
        "sid": sess.sid,
        "run_id": sess.run_id,
        "circle_id": sess.circle_id,
        "live_ai": settings.llm_configured,  # a model key is configured (runs may still fall back)
        "paypal_live": settings.paypal_configured,
        "frozen": frozen,
        "vault_seeded": vault_seeded,  # false -> the UI shows a "members not vaulted" banner
    }


@router.get("/session")
async def get_session_route(request: Request, response: Response) -> Any:
    try:
        sess = sessions.get_or_create_session(request, response)
    except WaitlistFull as e:
        from app.engine import story

        return JSONResponse(
            {
                "waitlist": True,
                "active": e.active,
                "max": e.maximum,
                "retry_after_s": 120,
                # read-only recorded run of the Judge Mode story while every live slot is taken
                "replay_url": story.REPLAY_URL,
                "replay_available": story.latest_recording() is not None,
            },
            status_code=429,
            headers={"Retry-After": "120"},
        )
    return session_shape(sess)


@router.get("/replay")
async def replay_route() -> Any:
    """The last recorded canonical Judge Mode run (events per story step). Read-only, no session needed: this is
    what a visitor past the session cap watches. 404 when nothing has been recorded or shipped yet."""
    from app.engine import story

    data = story.latest_recording()
    if data is None:
        raise HTTPException(status_code=404, detail="no recorded run yet")
    data["read_only"] = True
    return data


@router.post("/session/reset")
async def reset_session_route(sess: CurrentSession) -> dict[str, Any]:
    new = sessions.reset_session(sess.sid)
    return session_shape(new)


@router.get("/circles/{circle_id}")
async def get_circle(circle_id: str, sess: CurrentSession) -> dict[str, Any]:
    # A session only sees its own circle (the masked public view is /mcp). 403 before 404 so circle ids
    # cannot be probed for existence.
    sessions.owns_circle(sess, circle_id)
    try:
        return state.board_model(circle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="circle not found") from None


def start_round_task(
    kind: str,
    circle_id: str,
    n: int,
    sess: Session,
    *,
    fallback_engine: bool = False,
    request: Request | None = None,
) -> dict[str, Any]:
    """Shared by the collect/payout routes and Judge Mode's run_round."""
    sessions.owns_circle(sess, circle_id)
    if state.get_round(circle_id, n) is None:
        raise HTTPException(status_code=404, detail=f"round {n} not found")
    if not get_settings().paypal_configured:
        # Without sandbox credentials every charge/payout can only fail with 401; refuse honestly instead of
        # burning each member's attempt counter.
        raise HTTPException(status_code=503, detail="PayPal sandbox credentials not configured")
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
        kind,
        circle_id,
        n,
        sid=sess.sid,
        run_id=sess.run_id,
        fallback_engine=fallback_engine,
        live_allowed=tasks.live_allowed_for(request),  # counted only once every check above passed
    )
    log.info("task %s started kind=%s circle=%s round=%s", rec.task_id, kind, circle_id, n)
    return {"task_id": rec.task_id, "kind": kind, "circle_id": circle_id, "round_n": n, "agent": available}


@router.post("/circles/{circle_id}/rounds/{n}/collect", status_code=202)
async def collect_route(circle_id: str, n: int, sess: CurrentSession, request: Request) -> dict[str, Any]:
    return start_round_task("collect_round", circle_id, n, sess, request=request)


@router.post("/circles/{circle_id}/rounds/{n}/payout", status_code=202)
async def payout_route(circle_id: str, n: int, sess: CurrentSession, request: Request) -> dict[str, Any]:
    return start_round_task("payout", circle_id, n, sess, request=request)


@router.get("/tasks/{task_id}")
async def task_route(task_id: str, sess: CurrentSession) -> dict[str, Any]:
    status = tasks.task_status(task_id)
    rec = tasks._TASKS.get(task_id)
    if status is None or rec is None or rec.sid != sess.sid:  # another session's task reads as unknown
        raise HTTPException(status_code=404, detail="unknown task")
    return status
