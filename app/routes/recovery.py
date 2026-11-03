"""Recovery path routes (B6) and its Judge Mode actions.

POST /api/circles/{circle_id}/recovery/start   -> 202 {"task_id", "kind": "recover", "round_n", "member", "agent"}
GET  /api/circles/{circle_id}/recovery         -> recovery view (remedies, votes, consents, approval, invoice + QR
                                                  data, commitment, inbox)
GET  /api/circles/{circle_id}/recovery/qr      -> the invoice QR code as image/png (404 when none)
POST /api/judge/reply_as_ravi        {circle_id, text?}   Ravi's reply (default: the Hindi "salary comes Friday")
POST /api/judge/vote_as_members      {circle_id}          yes votes until quorum (non-affected members first)
POST /api/judge/consent_as_affected  {circle_id}          the short member and each swap partner consent
POST /api/judge/approve_as_priya     {circle_id}          the organizer's one tap -> execution (409 until ready)
POST /api/judge/mark_invoice_paid    {circle_id}          simulates INVOICING.INVOICE.PAID (labelled "simulated")
POST /api/judge/simulate_round3_decline {circle_id}       no live sandbox: round 3 settled with labelled REPLAY- ids
                                                          and Ravi DECLINED (simulated), so recovery can be shown

This router is included before app.routes.judge so these fixed /api/judge/* paths win over its {action} route.
"""

from __future__ import annotations

import base64
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.charter import quorum_tuple
from app.engine import inbox, recovery, sessions, state, tasks
from app.engine.events import get_event_bus
from app.engine.sessions import require_session
from app.models import Session

CurrentSession = Annotated[Session, Depends(require_session)]

log = logging.getLogger("turnkeeper.routes.recovery")

router = APIRouter(tags=["recovery"])

JUDGE_ACTIONS = (
    "reply_as_ravi",
    "vote_as_members",
    "consent_as_affected",
    "approve_as_priya",
    "mark_invoice_paid",
    "simulate_round3_decline",
)
DEMO_MEMBER = "ravi"
DEMO_ROUND = 3


class StartBody(BaseModel):
    round_n: int | None = Field(default=None, ge=1)
    member: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,15}$")


class RecoveryJudgeBody(BaseModel):
    circle_id: str
    text: str | None = Field(default=None, max_length=inbox.TEXT_MAX_CHARS)


def _publisher(sess: Session):
    return get_event_bus().publisher(sess.sid)


def _active_set(circle_id: str) -> Any:
    row = recovery.latest_set(circle_id)
    if row is None:
        raise HTTPException(status_code=409, detail="no remedy proposal yet; start the recovery first")
    return row


def _taint(circle_id: str, action: str) -> None:
    """A direct Judge action during a story run: that run will not become the public canonical replay
    (no-op for the story's own step executors and when no story is in progress)."""
    from app.engine import story

    story.taint(circle_id, action)


def _conflict(e: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


# --- recovery ---------------------------------------------------------------------------------------


@router.post("/api/circles/{circle_id}/recovery/start", status_code=202)
async def start_recovery(
    circle_id: str, sess: CurrentSession, request: Request, body: StartBody | None = None
) -> dict[str, Any]:
    sessions.owns_circle(sess, circle_id)
    body = body or StartBody()
    round_n, member = body.round_n, body.member
    if round_n is None or member is None:
        found = recovery.find_shortfall(circle_id)
        if found is None:
            raise HTTPException(status_code=409, detail="no declined contribution to recover")
        round_n = round_n or found[0]
        member = member or found[1]
    if state.get_round(circle_id, round_n) is None:
        raise HTTPException(status_code=404, detail=f"round {round_n} not found")
    if (why := recovery.shortfall_problem(circle_id, round_n, member)) is not None:
        # A named member/round must really be short: no remedy (and no buffer advance) without a decline.
        raise HTTPException(status_code=409, detail=why)
    running = tasks.running_for(circle_id)
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail={"task_id": running.task_id, "message": "a run is already in progress for this circle"},
        )
    available, _why = tasks.agent_available()

    async def _runner(rec: Any, run_id: str, publish: Any, fallback_engine: bool) -> None:
        await recovery.recover_runner(rec, run_id, publish, fallback_engine, member=member)

    rec = tasks.start_task(
        "recover",
        circle_id,
        round_n,
        sid=sess.sid,
        run_id=sess.run_id,
        runner=_runner,
        live_allowed=tasks.live_allowed_for(request),
    )
    log.info("recover task %s started circle=%s round=%s member=%s", rec.task_id, circle_id, round_n, member)
    return {
        "task_id": rec.task_id,
        "kind": "recover",
        "circle_id": circle_id,
        "round_n": round_n,
        "member": member,
        "agent": available,
    }


@router.get("/api/circles/{circle_id}/recovery")
async def get_recovery(circle_id: str, sess: CurrentSession) -> dict[str, Any]:
    sessions.owns_circle(sess, circle_id)
    view = recovery.recovery_view(circle_id, with_qr=True)
    member = view.get("member")
    view["inbox"] = (
        inbox.list_messages(circle_id, member=member, limit=20)
        if member
        else inbox.list_messages(circle_id, limit=20)
    )
    return view


@router.get("/api/circles/{circle_id}/recovery/injection-check")
async def get_injection_check(circle_id: str, sess: CurrentSession) -> dict[str, Any]:
    """Amounts the short member typed vs the proposal's amounts, with the computed policy verdict (read-only)."""
    sessions.owns_circle(sess, circle_id)
    return recovery.injection_check(circle_id)


@router.get("/api/circles/{circle_id}/recovery/qr")
async def get_invoice_qr(circle_id: str, sess: CurrentSession) -> Response:
    sessions.owns_circle(sess, circle_id)
    row = recovery.current_invoice(circle_id)
    if row is None or not row.invoice_qr or "," not in row.invoice_qr:
        raise HTTPException(status_code=404, detail="no invoice QR code")
    head, data = row.invoice_qr.split(",", 1)
    media = head[5:].split(";")[0] if head.startswith("data:") else "image/png"
    if media != "image/png":  # never serve a stored type that a browser could treat as a document
        raise HTTPException(status_code=404, detail="no invoice QR code")
    return Response(content=base64.b64decode(data), media_type="image/png")


# --- Judge Mode -------------------------------------------------------------------------------------


@router.post("/api/judge/reply_as_ravi")
async def reply_as_ravi(body: RecoveryJudgeBody, sess: CurrentSession) -> dict[str, Any]:
    sessions.owns_circle(sess, body.circle_id)
    _taint(body.circle_id, "reply_as_ravi")
    text = (body.text or "").strip() or inbox.DEFAULT_RAVI_REPLY
    lang = "hi" if any("ऀ" <= ch <= "ॿ" for ch in text) else "en"
    try:
        msg = inbox.add_message(
            body.circle_id, DEMO_MEMBER, text, lang=lang, direction="in", source="judge", round_n=DEMO_ROUND
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    circle = state.load_circle(body.circle_id)
    from app.engine.clock import VirtualClock

    rule = next((m.payday_rule for m in state.members_of(body.circle_id) if m.handle == DEMO_MEMBER), None)
    payday = inbox.parse_payday(text, today=VirtualClock(circle).today(), payday_rule=rule)
    publish = _publisher(sess)
    await publish({"type": "toast", "level": "info", "text": f"Ravi replied ({lang})"})
    await publish({"type": "board"})
    return {
        "ok": True,
        "action": "reply_as_ravi",
        "message": msg,
        "translation": inbox.DEFAULT_RAVI_REPLY_EN if text == inbox.DEFAULT_RAVI_REPLY else None,
        "payday": payday.isoformat() if payday else None,
    }


@router.post("/api/judge/vote_as_members")
async def vote_as_members(body: RecoveryJudgeBody, sess: CurrentSession) -> dict[str, Any]:
    sessions.owns_circle(sess, body.circle_id)
    _taint(body.circle_id, "vote_as_members")
    row = _active_set(body.circle_id)
    charter = state.load_charter(body.circle_id)
    k, _n = quorum_tuple(charter)
    affected = set(recovery.domain_set(row).affected_members()) | {row.member}
    order = [m.handle for m in charter.members if m.handle not in affected]
    order += [m.handle for m in charter.members if m.handle in affected]
    publish = _publisher(sess)
    try:
        status = recovery.vote_status(body.circle_id, row.id)
        voted = {v["member"] for v in status["votes"]["voters"] if v["choice"] != "pending"}
        cast: list[str] = []
        for handle in order:
            if status["votes"]["yes"] >= k:
                break
            if handle in voted:
                continue
            status = await recovery.cast_vote(
                body.circle_id, row.id, handle, "yes", source="judge", publish=publish
            )
            cast.append(handle)
    except recovery.RecoveryError as e:
        raise _conflict(e) from None
    await publish(
        {
            "type": "toast",
            "level": "info",
            "text": f"Votes: {status['votes']['yes']}/{k} yes ({', '.join(cast) or 'no new votes'})",
        }
    )
    return {
        "ok": True,
        "action": "vote_as_members",
        "cast": cast,
        **{k2: v for k2, v in status.items() if k2 != "ok"},
    }


@router.post("/api/judge/consent_as_affected")
async def consent_as_affected(body: RecoveryJudgeBody, sess: CurrentSession) -> dict[str, Any]:
    sessions.owns_circle(sess, body.circle_id)
    _taint(body.circle_id, "consent_as_affected")
    row = _active_set(body.circle_id)
    publish = _publisher(sess)
    needed = recovery.domain_set(row).affected_members()
    try:
        status = recovery.vote_status(body.circle_id, row.id)
        for handle in needed:
            status = await recovery.record_consent(
                body.circle_id, row.id, handle, "yes", source="judge", publish=publish
            )
    except recovery.RecoveryError as e:
        raise _conflict(e) from None
    return {
        "ok": True,
        "action": "consent_as_affected",
        "consented": needed,
        **{k: v for k, v in status.items() if k != "ok"},
    }


@router.post("/api/judge/approve_as_priya")
async def approve_as_priya(body: RecoveryJudgeBody, sess: CurrentSession) -> dict[str, Any]:
    sessions.owns_circle(sess, body.circle_id)
    _taint(body.circle_id, "approve_as_priya")
    row = _active_set(body.circle_id)
    publish = _publisher(sess)
    result = await recovery.approve(
        body.circle_id, row.id, by=recovery.ORGANIZER, execute=True, publish=publish
    )
    if not result.get("ok") and result.get("denied"):
        raise HTTPException(status_code=409, detail=result["denied"])
    return {"action": "approve_as_priya", **result}


@router.post("/api/judge/mark_invoice_paid")
async def mark_invoice_paid(body: RecoveryJudgeBody, sess: CurrentSession) -> dict[str, Any]:
    sessions.owns_circle(sess, body.circle_id)
    _taint(body.circle_id, "mark_invoice_paid")
    try:
        result = await recovery.simulate_invoice_paid(body.circle_id, publish=_publisher(sess))
    except recovery.RecoveryError as e:
        raise _conflict(e) from None
    return {"ok": bool(result.get("handled")), "action": "mark_invoice_paid", "simulated": True, **result}


@router.post("/api/judge/simulate_round3_decline")
async def simulate_round3_decline(body: RecoveryJudgeBody, sess: CurrentSession) -> dict[str, Any]:
    """Stand-in for Run Round 3 when the sandbox cannot charge (no credentials or no seeded vault ids)."""
    sessions.owns_circle(sess, body.circle_id)
    _taint(body.circle_id, "simulate_round3_decline")
    from app.routes.circles import circle_vault_seeded

    if get_settings().paypal_configured and circle_vault_seeded(body.circle_id):
        raise HTTPException(
            status_code=409,
            detail="the PayPal sandbox is live for this circle: use Run Round 3 (with 'Ravi declines' on)",
        )
    running = tasks.running_for(body.circle_id)
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail={"task_id": running.task_id, "message": "a run is already in progress for this circle"},
        )
    try:
        result = await recovery.simulate_round_decline(
            body.circle_id, DEMO_ROUND, DEMO_MEMBER, publish=_publisher(sess)
        )
    except recovery.RecoveryError as e:
        raise _conflict(e) from None
    return {"action": "simulate_round3_decline", **result}
