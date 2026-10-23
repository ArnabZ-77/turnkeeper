"""Judge Mode story routes (included by app.routes.judge under /api/judge).

GET  /api/judge/story          -> the story view: steps with status, the current step and the next action label
POST /api/judge/story/{step}   -> run `step` when it is the next one ("next" = whatever is next; "reset" always)

Steps: reset, advance_to_t3 (forecast), run_round3, reply_as_ravi, start_recovery, consents, votes, approve,
execute, payout, clean_round4. Steps that start a background run (run_round3 and clean_round4 on a live sandbox,
start_recovery) report `running` until the task finishes; the story then checks the step's outcome (for example
"ravi DECLINED, the others COMPLETED") before it counts the step as done. Without a live sandbox the round steps
are simulated and labelled so (no PayPal call, payout skipped).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import get_settings
from app.engine import recovery, rounds, sessions, state, story, tasks
from app.engine.clock import advance_clock
from app.engine.common import as_utc
from app.engine.events import get_event_bus
from app.engine.sessions import require_session
from app.models import Session

CurrentSession = Annotated[Session, Depends(require_session)]

log = logging.getLogger("turnkeeper.routes.judge_story")

router = APIRouter(tags=["judge-story"])

DEMO_ROUND = 3
CLEAN_ROUND = 4
DEMO_MEMBER = "ravi"
FORECAST_DAYS = 3

# One story step at a time per circle: a double-click or a second tab gets 409 instead of running side effects twice.
_STEP_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(circle_id: str) -> asyncio.Lock:
    lock = _STEP_LOCKS.get(circle_id)
    if lock is None:
        if len(_STEP_LOCKS) > 512:  # keep the registry bounded (idle locks only)
            for cid in [c for c, lk in _STEP_LOCKS.items() if not lk.locked()][:256]:
                _STEP_LOCKS.pop(cid, None)
        lock = _STEP_LOCKS[circle_id] = asyncio.Lock()
    return lock


def can_charge_live(circle_id: str) -> bool:
    """The sandbox can charge this circle: credentials plus a real vault id for every member."""
    from app.routes.circles import circle_vault_seeded

    return bool(get_settings().paypal_configured) and circle_vault_seeded(circle_id)


def _current(sess: Session) -> Session:
    """The session as stored now (a story reset moves it to a new circle)."""
    return sessions.resolve_session(sess.sid) or sess


def _running(circle_id: str) -> dict[str, Any] | None:
    row = story.load(circle_id)
    if row is None or not row.pending_step:
        return None
    rec = tasks._TASKS.get(row.pending_task_id or "")
    if rec is not None and not rec.done:
        return {"step": row.pending_step, "task_id": rec.task_id}
    return None


# --- outcome checks for background steps ---------------------------------------------------------


def _round_statuses(circle_id: str, round_n: int) -> dict[str, str]:
    return {c.member_handle: c.status for c in state.round_contributions(circle_id, round_n)}


def _check(step: str, circle_id: str) -> tuple[bool, str]:
    if step == "run_round3":
        st = _round_statuses(circle_id, DEMO_ROUND)
        others = {h: s for h, s in st.items() if h != DEMO_MEMBER}
        ok = st.get(DEMO_MEMBER) == "DECLINED" and all(s == "COMPLETED" for s in others.values())
        done = sum(1 for s in others.values() if s == "COMPLETED")
        return ok, f"Round {DEMO_ROUND}: {done}/{len(others)} captured, ravi {st.get(DEMO_MEMBER)}"
    if step == "start_recovery":
        row = recovery.latest_set(circle_id)
        ok = row is not None and row.status in ("voting", "approved")
        return ok, (
            f"Remedy proposal {row.status} ({row.source})"
            if row is not None
            else "no remedy proposal was made"
        )
    if step == "clean_round4":
        st = _round_statuses(circle_id, CLEAN_ROUND)
        rnd = state.get_round(circle_id, CLEAN_ROUND)
        ok = bool(st) and all(s == "COMPLETED" for s in st.values())
        paid = rnd.status if rnd else "?"
        return (
            ok,
            f"Round {CLEAN_ROUND}: {sum(1 for s in st.values() if s == 'COMPLETED')}/{len(st)} captured; {paid}",
        )
    return True, step


async def settle(sess: Session) -> None:
    """Close a finished background step: record it when its outcome holds, else free the step for a retry."""
    cid = sess.circle_id
    row = story.load(cid)
    if row is None or not row.pending_step or _running(cid) is not None:
        return
    step = row.pending_step
    rec = tasks._TASKS.get(row.pending_task_id or "")
    ok, summary = _check(step, cid)
    if not ok:
        story.clear_pending(cid)
        await get_event_bus().publish(
            sess.sid,
            {"type": "toast", "level": "warn", "text": f"Story step '{step}' did not finish: {summary}"},
        )
        return
    mode = (rec.mode if rec else None) or ("live" if can_charge_live(cid) else "simulated")
    story.record_step(cid, step, summary=summary, mode=str(mode), events=story.capture(sess.sid, step))
    await _maybe_finish(sess)


async def _maybe_finish(sess: Session) -> None:
    row = story.load(sess.circle_id)
    if row is not None and story.next_step(row) is None:
        story.save_recording(
            sess.circle_id, paypal_mode="live" if can_charge_live(sess.circle_id) else "simulated"
        )
        await get_event_bus().publish(
            sess.sid,
            {"type": "toast", "level": "success", "text": "Story complete: recorded as the replay run"},
        )


# --- step executors ------------------------------------------------------------------------------------
# Each returns (result, task_id or None, mode, summary). A task_id means "running in the background".

StepOut = tuple[dict[str, Any], str | None, str, str]


def _publish(sess: Session):
    return get_event_bus().publisher(sess.sid)


def _set_id(circle_id: str) -> str:
    row = recovery.latest_set(circle_id)
    if row is None:
        raise HTTPException(status_code=409, detail="no remedy proposal yet; start the recovery first")
    return row.id


def _advance_to(circle_id: str, target) -> float:
    now = as_utc(state.load_circle(circle_id).virtual_now)
    if now >= target:
        return 0.0
    days = (target - now).total_seconds() / 86400
    advance_clock(circle_id, days)
    return days


async def _step_advance_to_t3(sess: Session, request: Request) -> StepOut:
    cid = sess.circle_id
    rnd = state.get_round(cid, DEMO_ROUND)
    if rnd is None:
        raise HTTPException(status_code=404, detail=f"round {DEMO_ROUND} not found")
    days = _advance_to(cid, as_utc(rnd.due_at) - timedelta(days=FORECAST_DAYS))
    publish = _publish(sess)
    from app.engine import forecast_job

    # The badge is backed by the forecaster only (PLAN section 5): no fallback that reads stored badges.
    forecasts = await forecast_job.run_forecast(
        cid, DEMO_ROUND, publish=publish, live_allowed=tasks.live_allowed_for(request)
    )
    flagged = [f["member"] for f in forecasts if f.get("flagged")]
    modes = {str(f.get("mode")) for f in forecasts if f.get("flagged") and f.get("mode")}
    await publish({"type": "board"})
    mode = "rules" if not modes or modes == {"rules"} else ("replay" if modes == {"replay"} else "live")
    summary = f"T-3 for round {DEMO_ROUND} (+{days:g} d); flagged: {', '.join(flagged) or 'nobody'} ({mode})"
    return {"flagged": flagged, "forecasts": forecasts, "advanced_days": days}, None, mode, summary


async def _step_run_round3(sess: Session, request: Request) -> StepOut:
    from app.routes.circles import start_round_task

    cid = sess.circle_id
    state.set_flag(cid, "ravi_declines", True)
    if can_charge_live(cid):
        started = start_round_task(
            "collect_round", cid, DEMO_ROUND, sess, fallback_engine=True, request=request
        )
        return started, started["task_id"], "live", "Round 3 collection started"
    try:
        res = await recovery.simulate_round_decline(cid, DEMO_ROUND, DEMO_MEMBER, publish=_publish(sess))
    except recovery.RecoveryError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    ok, summary = _check("run_round3", cid)
    if not ok:
        raise HTTPException(status_code=409, detail=summary)
    return res, None, "simulated", summary + " (simulated, no PayPal call)"


async def _step_reply(sess: Session, request: Request) -> StepOut:
    from app.routes.recovery import RecoveryJudgeBody, reply_as_ravi

    res = await reply_as_ravi(RecoveryJudgeBody(circle_id=sess.circle_id), sess)
    return res, None, "judge", f"Ravi replied; payday {res.get('payday')}"


async def _step_start_recovery(sess: Session, request: Request) -> StepOut:
    from app.routes.recovery import StartBody, start_recovery

    res = await start_recovery(
        sess.circle_id, sess, request, StartBody(round_n=DEMO_ROUND, member=DEMO_MEMBER)
    )
    return res, res["task_id"], "rules", "Recovery started"


async def _step_consents(sess: Session, request: Request) -> StepOut:
    from app.routes.recovery import RecoveryJudgeBody, consent_as_affected

    res = await consent_as_affected(RecoveryJudgeBody(circle_id=sess.circle_id), sess)
    return res, None, "judge", f"Consents: {', '.join(res.get('consented') or []) or 'none needed'}"


async def _step_votes(sess: Session, request: Request) -> StepOut:
    from app.routes.recovery import RecoveryJudgeBody, vote_as_members

    res = await vote_as_members(RecoveryJudgeBody(circle_id=sess.circle_id), sess)
    votes = res.get("votes") or {}
    if not votes.get("passed"):
        raise HTTPException(status_code=409, detail=f"vote did not pass: {votes}")
    return res, None, "judge", f"Votes {votes.get('yes')}/{votes.get('needed')} yes"


async def _step_approve(sess: Session, request: Request) -> StepOut:
    cid = sess.circle_id
    res = await recovery.approve(
        cid, _set_id(cid), by=recovery.ORGANIZER, execute=False, publish=_publish(sess)
    )
    if not res.get("ok", True) and res.get("denied"):
        raise HTTPException(status_code=409, detail=res["denied"])
    return res, None, "judge", "Priya approved (one tap)"


async def _step_execute(sess: Session, request: Request) -> StepOut:
    cid = sess.circle_id
    res = await recovery.execute_remedies(cid, _set_id(cid), publish=_publish(sess), pay_after=False)
    if not res.get("ok"):
        raise HTTPException(
            status_code=409, detail=res.get("denied") or res.get("error") or "execution failed"
        )
    kinds = [r.get("type") for r in res.get("results") or [] if isinstance(r, dict)]
    return res, None, "rules", f"Executed: {', '.join(k for k in kinds if k) or res.get('status')}"


async def _step_payout(sess: Session, request: Request) -> StepOut:
    cid = sess.circle_id
    rnd = state.get_round(cid, DEMO_ROUND)
    if rnd is not None and rnd.status == "PAID":
        return (
            {"ok": True, "already": True, "status": rnd.payout_status},
            None,
            "judge",  # this step made no PayPal call
            f"Round {DEMO_ROUND} was already paid (no new payout)",
        )
    simulated = any(
        (c.request_id or "").startswith(rounds.SIMULATED_REQUEST_PREFIX)
        for c in state.round_contributions(cid, DEMO_ROUND)
    )
    if simulated or not get_settings().paypal_configured:
        why = "the round's charges were simulated" if simulated else "no PayPal sandbox credentials"
        await _publish(sess)(
            {
                "type": "trace",
                "kind": "system",
                "source": "payout",
                "text": f"Payout skipped: {why} (no PayPal call)",
            }
        )
        return {"ok": True, "skipped": why}, None, "simulated", f"Payout skipped: {why}"
    res = await rounds.pay_round(cid, DEMO_ROUND, publish=_publish(sess))
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("denied") or res.get("label") or res.get("error"))
    return res, None, "live", f"Pot {res.get('amount')} -> {res.get('recipient')}: {res.get('status')}"


async def _step_clean_round4(sess: Session, request: Request) -> StepOut:
    from app.routes.circles import start_round_task

    cid = sess.circle_id
    rnd = state.get_round(cid, CLEAN_ROUND)
    if rnd is None:
        raise HTTPException(status_code=404, detail=f"round {CLEAN_ROUND} not found")
    _advance_to(cid, as_utc(rnd.due_at))
    state.set_flag(cid, "ravi_declines", False)
    if can_charge_live(cid):
        started = start_round_task(
            "collect_round", cid, CLEAN_ROUND, sess, fallback_engine=True, request=request
        )
        return started, started["task_id"], "live", "Round 4 collection started"
    res = await rounds.simulate_clean_round(cid, CLEAN_ROUND, publish=_publish(sess))
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("error"))
    ok, summary = _check("clean_round4", cid)
    return res, None, "simulated", summary + " (simulated, payout skipped)"


EXECUTORS = {
    "advance_to_t3": _step_advance_to_t3,
    "run_round3": _step_run_round3,
    "reply_as_ravi": _step_reply,
    "start_recovery": _step_start_recovery,
    "consents": _step_consents,
    "votes": _step_votes,
    "approve": _step_approve,
    "execute": _step_execute,
    "payout": _step_payout,
    "clean_round4": _step_clean_round4,
}


# --- routes ---------------------------------------------------------------------------------------------


@router.get("/story")
async def get_story(sess: CurrentSession) -> dict[str, Any]:
    sess = _current(sess)
    try:
        await settle(sess)
    except story.StoryError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    return story.story_view(
        sess.circle_id, live=can_charge_live(sess.circle_id), running=_running(sess.circle_id)
    )


@router.post("/story/{step}")
async def post_story(step: str, sess: CurrentSession, request: Request) -> dict[str, Any]:
    sess = _current(sess)
    lock = _lock_for(sess.circle_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="a story step is already running")
    async with lock:
        try:
            return await _post_story_locked(step, sess, request)
        except story.StoryError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail) from None


async def _post_story_locked(step: str, sess: Session, request: Request) -> dict[str, Any]:
    await settle(sess)
    cid = sess.circle_id
    nxt = story.next_step(story.load(cid))
    if step == "next":
        if nxt is None:
            raise HTTPException(status_code=409, detail="the story is complete; reset to start again")
        step = nxt
    if step not in story.STEP_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown story step {step!r}")
    running = _running(cid)
    if step != "reset":
        if running is not None:
            raise HTTPException(
                status_code=409, detail={"message": "a story step is still running", **running}
            )
        if step != nxt:
            raise HTTPException(status_code=409, detail=f"the next story step is {nxt!r}, not {step!r}")

    if step == "reset":
        # Restart always works: reset_session closes the old circle, and background runs still going on it stop
        # reaching this session's stream (tasks.make_publisher checks the session's current circle).
        from app.routes.circles import session_shape

        story.clear_pending(cid)
        await story.mark_step_start(sess.sid, cid, "reset")
        new = sessions.reset_session(sess.sid)
        story.begin(new.circle_id, new.sid, new.run_id)
        bus = get_event_bus()
        await bus.publish(
            sess.sid, {"type": "toast", "level": "info", "text": "Story reset: new circle, new run id"}
        )
        await bus.publish(sess.sid, {"type": "board"})
        story.record_step(
            new.circle_id,
            "reset",
            summary=f"New circle {new.circle_id}, run {new.run_id}",
            mode="judge",
            events=story.capture(sess.sid, "reset"),
        )
        live = can_charge_live(new.circle_id)
        return {
            "ok": True,
            "step": "reset",
            "session": session_shape(new),
            "story": story.story_view(new.circle_id, live=live),
        }

    if story.load(cid) is None:  # defensive: next_step() is "reset" for a circle without a story
        raise HTTPException(status_code=409, detail="reset the story first")
    await story.mark_step_start(sess.sid, cid, step)
    result, task_id, mode, summary = await EXECUTORS[step](sess, request)
    if task_id is not None:
        story.set_pending(cid, step, task_id)
    else:
        story.record_step(
            cid, step, summary=summary, mode=mode, events=story.capture(sess.sid, step), result=result
        )
        await _maybe_finish(sess)
    live = can_charge_live(cid)
    return {
        "ok": True,
        "step": step,
        "mode": mode,
        "summary": summary,
        "task_id": task_id,
        "result": result,
        "story": story.story_view(cid, live=live, running=_running(cid)),
    }
