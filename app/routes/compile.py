"""Charter compiler and freeze (PLAN.md section 3 steps 1-3).

POST /api/circles/compile                    {text, lang?} -> draft Charter + explanations + problems + draft_id
GET  /api/circles/compile/demo?lang=en|hi    -> the demo rules text (the UI's "paste example" button)
POST /api/circles/{circle_id}/charter/freeze {draft_id?, confirm?} -> canonical hash + billing_plan preview;
                                             confirm=true records the organizer's approval (and, for a draft that
                                             differs from the circle's charter, creates a new circle to invite into)
GET  /api/circles/{circle_id}/charter        -> the circle's charter, hash, fees and latest organizer approval
"""

import logging
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import select

from app.agent import charter as compiler
from app.db import session_scope
from app.domain.charter import Charter
from app.engine import join as jn
from app.engine import sessions, state, tasks
from app.engine.sessions import require_session
from app.models import CharterDraftRow, Session

CurrentSession = Annotated[Session, Depends(require_session)]

log = logging.getLogger("turnkeeper.routes.compile")

router = APIRouter(prefix="/api/circles", tags=["charter"])


class CompileBody(BaseModel):
    text: str = Field(min_length=1, max_length=compiler.MAX_TEXT_LEN)
    lang: str | None = Field(default=None, max_length=12)


class FreezeBody(BaseModel):
    draft_id: str | None = Field(default=None, max_length=32)
    confirm: bool = False


@router.post("/compile")
async def compile_route(body: CompileBody, sess: CurrentSession, request: Request) -> dict[str, Any]:
    # A recorded answer replays without spending this IP's live-AI budget; otherwise count one live attempt.
    live_allowed = (
        True if compiler.replay_available(body.text, body.lang) else tasks.live_allowed_for(request)
    )
    result = await compiler.compile_charter(body.text, body.lang, live_allowed=live_allowed)
    draft_id = f"cd_{secrets.token_hex(4)}"
    with session_scope() as db:
        db.add(
            CharterDraftRow(
                id=draft_id,
                sid=sess.sid,
                circle_id=sess.circle_id,
                json=result.model_dump_json(),
                charter_sha256=result.charter_sha256,
                mode=result.mode,
                source=result.source,
            )
        )
    try:
        circle_sha = state.load_circle(sess.circle_id).charter_sha256
    except LookupError:
        circle_sha = None
    return {
        **result.model_dump(mode="json"),
        "draft_id": draft_id,
        "matches_circle": bool(result.charter_sha256 and result.charter_sha256 == circle_sha),
        "live_ai_allowed": live_allowed,
    }


@router.get("/compile/demo")
async def demo_text(lang: str = "en") -> dict[str, Any]:
    lang = "hi" if lang == "hi" else "en"
    return {"lang": lang, "text": compiler.demo_rules(lang)}


def _draft_charter(draft_id: str, sid: str) -> Charter:
    with session_scope() as db:
        row = db.get(CharterDraftRow, draft_id)
    if row is None or row.sid != sid:
        raise HTTPException(status_code=404, detail="draft not found")
    result = compiler.CompileResult.model_validate_json(row.json)
    charter = result.charter_model()
    if not result.ok or charter is None:
        raise HTTPException(
            status_code=422,
            detail={"message": "this draft is not a valid charter", "problems": result.problems},
        )
    return charter


def _manage(sess: Session, circle_id: str) -> None:
    if not jn.session_manages(sess.sid, sess.circle_id, circle_id):
        raise HTTPException(status_code=403, detail="circle does not belong to this session")


@router.post("/{circle_id}/charter/freeze")
async def freeze_route(
    circle_id: str, body: FreezeBody, sess: CurrentSession, request: Request
) -> dict[str, Any]:
    _manage(sess, circle_id)
    try:
        circle = state.load_circle(circle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="circle not found") from None
    charter = _draft_charter(body.draft_id, sess.sid) if body.draft_id else state.charter_of(circle)
    preview = jn.freeze_preview(charter)
    sha = preview["charter_sha256"]
    matches = sha == circle.charter_sha256
    out: dict[str, Any] = {
        "ok": True,
        "circle_id": circle_id,
        "draft_id": body.draft_id,
        "matches_circle": matches,
        "confirmed": False,
        "created_circle": False,
        **preview,
        "approval": jn.latest_approval(circle_id) if matches else None,
    }
    if not body.confirm:
        return out
    ua = request.headers.get("user-agent", "")
    target, created = circle_id, False
    if not matches and body.draft_id:
        prior = jn.circle_for_draft(body.draft_id)
        if prior is not None:
            target = prior
        else:
            new = jn.create_circle(charter, run_id=sess.run_id, owner_sid=sess.sid, kind=jn.COMPILED_KIND)
            target, created = new.id, True
    if target != circle_id and not created:
        approval = jn.latest_approval(target)
    else:
        approval = jn.record_approval(target, sha, draft_id=body.draft_id, user_agent=ua)
    publish = sessions.circle_publisher(sess.circle_id)
    await publish(
        {"type": "toast", "level": "success", "text": f"Charter {sha[:12]} confirmed by the organizer"}
    )
    out.update(
        {
            "confirmed": True,
            "circle_id": target,
            "created_circle": created,
            "approval": approval,
            "invites": jn.invites_for(target),
        }
    )
    return out


@router.get("/{circle_id}/charter")
async def charter_route(circle_id: str, sess: CurrentSession) -> dict[str, Any]:
    _manage(sess, circle_id)
    try:
        circle = state.load_circle(circle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="circle not found") from None
    charter = state.charter_of(circle)
    with session_scope() as db:
        drafts = db.exec(
            select(CharterDraftRow.id)
            .where(CharterDraftRow.sid == sess.sid)
            .order_by(CharterDraftRow.created_at.desc())
        ).all()
    return {
        "ok": True,
        "circle_id": circle_id,
        "charter": charter.model_dump(mode="json"),
        "charter_sha256": circle.charter_sha256,
        "summary": jn.charter_summary(charter),
        "fees": jn.fees_summary(charter),
        "approval": jn.latest_approval(circle_id),
        "drafts": list(drafts)[:10],
    }
