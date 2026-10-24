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

ACTIONS = (
    "reset",
    "advance_clock",
    "run_round",
    "toggle_ravi_declines",
    "make_next_payout_unclaimed",
    "run_forecast",  # builder C (M3): force the T-3 forecast for {round_n} (default: the next open round)
    "reroute_unclaimed",  # builder E: UNCLAIMED pot -> wait, one cancel attempt, new batch r{rev+1} (background)
)


async def _forecast_after_clock(circle_id: str, sid: str, request: Request) -> list[dict[str, Any]]:
    """T-3 forecast job (app.engine.forecast_job); the live-AI budget is counted only when a round is due."""
    try:
        from app.engine import forecast_job, tasks

        if not forecast_job.due_rounds(circle_id):
            return []
        bus = get_event_bus()
        return await forecast_job.on_clock_advanced(
            circle_id, publish=bus.publisher(sid), live_allowed=tasks.live_allowed_for(request)
        )
    except Exception as e:  # noqa: BLE001 - a clock click never fails because of the forecaster
        log.warning("forecast after clock advance failed: %s", type(e).__name__)
        return []


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
    # Advanced controls change the circle's state mid-story: that run is then never the public canonical replay.
    from app.engine import story

    story.taint(body.circle_id, action)

    if action == "advance_clock":
        days = body.days if body.days is not None else 1.0
        new_now = advance_clock(body.circle_id, days)
        await bus.publish(
            sess.sid,
            {"type": "toast", "level": "info", "text": f"Clock advanced {days:g} day(s) to {iso(new_now)}"},
        )
        forecasts = await _forecast_after_clock(body.circle_id, sess.sid, request)
        await bus.publish(sess.sid, {"type": "board"})
        return {"ok": True, "action": action, "virtual_now": iso(new_now), "forecasts": forecasts}

    if action == "run_forecast":
        from app.engine import forecast_job, tasks

        round_n = body.round_n
        if round_n is None:
            open_rounds = [
                r["n"]
                for r in state.board_model(body.circle_id)["rounds"]
                if r["status"] in ("PENDING", "COLLECTING")
            ]
            if not open_rounds:
                raise HTTPException(status_code=409, detail="no open round to forecast")
            round_n = open_rounds[0]
        if state.get_round(body.circle_id, round_n) is None:
            raise HTTPException(status_code=404, detail=f"round {round_n} not found")
        forecasts = await forecast_job.run_forecast(
            body.circle_id,
            round_n,
            publish=bus.publisher(sess.sid),
            live_allowed=tasks.live_allowed_for(request),
            force=True,
        )
        return {"ok": True, "action": action, "round_n": round_n, "forecasts": forecasts}

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

    if action == "reroute_unclaimed":
        return _start_reroute(body.circle_id, body.round_n, sess)

    raise HTTPException(status_code=404, detail="unknown action")  # pragma: no cover


def _start_reroute(circle_id: str, round_n: int | None, sess: Session) -> dict[str, Any]:
    """Background run of rounds.reroute_unclaimed (it may wait up to ~60 s for the batch to finish)."""
    from app.engine import rounds, tasks

    if round_n is None:
        unclaimed = [
            r["n"]
            for r in state.board_model(circle_id)["rounds"]
            if (r["payout"] or {}).get("status") == "UNCLAIMED"
        ]
        if not unclaimed:
            raise HTTPException(status_code=409, detail="no UNCLAIMED payout to re-route")
        round_n = unclaimed[-1]
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        raise HTTPException(status_code=404, detail=f"round {round_n} not found")
    if (rnd.payout_status or "").upper() != "UNCLAIMED":
        raise HTTPException(
            status_code=409, detail=f"round {round_n} payout is {rnd.payout_status or 'not sent'}"
        )
    running = tasks.running_for(circle_id)
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail={"task_id": running.task_id, "message": "a run is already in progress for this circle"},
        )

    async def _runner(rec: Any, run_id: str, publish: Any, fallback_engine: bool) -> None:
        try:
            res = await rounds.reroute_unclaimed(rec.circle_id, rec.round_n, publish=publish)
            rec.ok = bool(res.get("ok"))
            rec.mode = "engine"
            rec.summary = (
                f"r{res.get('rev')} {res.get('status')} (cancel {res['cancel']['status']})"
                if res.get("ok")
                else str(res.get("denied") or res.get("label") or res.get("error"))
            )
        except Exception as e:  # noqa: BLE001 - surfaced to the UI
            log.exception("reroute task failed")
            rec.ok, rec.error = False, f"{type(e).__name__}: {e}"
        finally:
            from app.engine.common import utcnow

            rec.done, rec.finished_at = True, utcnow()
            await publish({"type": "board"})

    rec = tasks.start_task("reroute", circle_id, round_n, sid=sess.sid, run_id=sess.run_id, runner=_runner)
    return {"ok": True, "action": "reroute_unclaimed", "task_id": rec.task_id, "round_n": round_n}


# Judge Mode story ("Next step"): GET /api/judge/story, POST /api/judge/story/{step} (builder E)
from app.routes.judge_story import router as _story_router  # noqa: E402

router.include_router(_story_router)
