"""Judge Mode story: one "Next step" button walks the scripted demo (PLAN.md sections 3, 10 and 11).

This module keeps the story's progress per circle (`JudgeStoryRow`), captures the session's events per step from
the SSE bus, and turns a completed story into a read-only `ReplayRecording` (served at GET /api/replay, for
visitors who hit the session cap). The step executors live in app/routes/judge_story.py.
"""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import case
from sqlmodel import select

from app.db import session_scope
from app.engine.common import iso, utcnow
from app.engine.events import get_event_bus
from app.models import JudgeStoryRow, ReplayRecording

log = logging.getLogger("turnkeeper.engine.story")

# (step, label) in story order. Labels are what the single "Next step" button reads.
STEPS: tuple[tuple[str, str], ...] = (
    ("reset", "Reset demo (new circle, new run id)"),
    ("advance_to_t3", "Advance to T-3 (forecast)"),
    ("run_round3", "Run Round 3 (Ravi declines)"),
    ("reply_as_ravi", "Reply as Ravi (Hindi)"),
    ("start_recovery", "Start recovery"),
    ("consents", "Consent as the affected members"),
    ("votes", "Vote as members (quorum)"),
    ("approve", "Approve as Priya (one tap)"),
    ("execute", "Execute remedies"),
    ("payout", "Pay Round 3"),
    ("clean_round4", "Clean Round 4 (no clicks)"),
)
STEP_NAMES: tuple[str, ...] = tuple(s for s, _ in STEPS)
LABELS: dict[str, str] = dict(STEPS)
# Labels when the sandbox cannot charge this circle (no credentials or no seeded vault ids): honest wording.
SIMULATED_LABELS: dict[str, str] = {
    "run_round3": "Simulate Round 3 (no sandbox: Ravi's decline is simulated)",
    "payout": "Pay Round 3 (skipped: the round was simulated)",
    "clean_round4": "Simulate clean Round 4 (no sandbox)",
}
ASYNC_STEPS = frozenset({"run_round3", "start_recovery", "clean_round4"})  # may run as a background task

MARKER_TYPE = "story"
MAX_EVENTS_PER_STEP = 150
TEXT_LIMIT = 400
REPLAY_URL = "/api/replay"
SHIPPED_REPLAY = Path(__file__).resolve().parent.parent / "fixtures" / "replay_canonical.json"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+\d[\d\s-]{7,}\d")
REPLAY_LABEL = "Recorded replay of a canonical Judge Mode run: read-only, no PayPal or AI calls"


class StoryError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def label_for(step: str, *, live: bool = True) -> str:
    if not live and step in SIMULATED_LABELS:
        return SIMULATED_LABELS[step]
    return LABELS[step]


# --- persistence ------------------------------------------------------------------------------------


def load(circle_id: str) -> JudgeStoryRow | None:
    with session_scope() as db:
        return db.get(JudgeStoryRow, circle_id)


def done_steps(row: JudgeStoryRow | None) -> list[dict[str, Any]]:
    if row is None:
        return []
    try:
        data = json.loads(row.steps_json or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def next_step(row: JudgeStoryRow | None) -> str | None:
    n = len(done_steps(row))
    return STEP_NAMES[n] if n < len(STEP_NAMES) else None


def begin(circle_id: str, sid: str, run_id: str) -> JudgeStoryRow:
    """A fresh story for a (new) circle; replaces any earlier progress on it."""
    now = utcnow()
    with session_scope() as db:
        row = db.get(JudgeStoryRow, circle_id) or JudgeStoryRow(circle_id=circle_id, sid=sid)
        row.sid, row.run_id = sid, run_id
        row.steps_json = "[]"
        row.pending_step = row.pending_task_id = None
        row.started_at = row.updated_at = now
        row.completed_at = None
        row.tainted = False
        row.last_error_json = None
        db.add(row)
        return row


# Set while a story step executor runs: the story's own calls into the /api/judge/* handlers never taint it.
IN_STORY_STEP: contextvars.ContextVar[bool] = contextvars.ContextVar("tk_in_story_step", default=False)


def taint(circle_id: str, reason: str, *, force: bool = False) -> bool:
    """An Advanced Judge control was used while this circle's story is in progress: the finished run will not
    become the public canonical replay (its events would carry that state). Returns True when it tainted.
    Calls made by the story's own step executors (IN_STORY_STEP) do not count unless ``force``."""
    if IN_STORY_STEP.get() and not force:
        return False
    with session_scope() as db:
        row = db.get(JudgeStoryRow, circle_id)
        if row is None or row.completed_at is not None or row.tainted:
            return False
        row.tainted = True
        row.updated_at = utcnow()
        db.add(row)
    log.info("story on circle %s tainted by %s: its recording will not be canonical", circle_id, reason)
    return True


def set_error(circle_id: str, step: str, error: dict[str, Any]) -> None:
    """Keep the last failed attempt at `step` on the story (the toast is transient; GET /story shows this)."""
    entry = {"step": step, "at": iso(utcnow()), **scrub(error)}
    with session_scope() as db:
        row = db.get(JudgeStoryRow, circle_id)
        if row is None:
            return
        row.last_error_json = json.dumps(entry, default=str)
        row.updated_at = utcnow()
        db.add(row)


def last_error(row: JudgeStoryRow | None) -> dict[str, Any] | None:
    if row is None or not row.last_error_json:
        return None
    try:
        data = json.loads(row.last_error_json)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def set_pending(circle_id: str, step: str, task_id: str | None) -> None:
    with session_scope() as db:
        row = db.get(JudgeStoryRow, circle_id)
        if row is None:
            raise LookupError(f"no story for circle {circle_id}")
        row.pending_step, row.pending_task_id = step, task_id
        row.updated_at = utcnow()
        db.add(row)


def clear_pending(circle_id: str) -> None:
    with session_scope() as db:
        row = db.get(JudgeStoryRow, circle_id)
        if row is not None:
            row.pending_step = row.pending_task_id = None
            row.updated_at = utcnow()
            db.add(row)


def record_step(
    circle_id: str,
    step: str,
    *,
    summary: str,
    mode: str,
    events: list[dict[str, Any]],
    result: dict[str, Any] | None = None,
    paypal: str | None = None,
    ai: str | None = None,
) -> dict[str, Any]:
    """Append a finished step (it must be the story's next step) and clear any pending task.

    ``mode`` is the one-word label the UI shows; ``paypal`` (live | simulated | None = no PayPal call) and ``ai``
    (live | replay | rules | None = no model involved) say separately how the money and the AI side ran."""
    with session_scope() as db:
        row = db.get(JudgeStoryRow, circle_id)
        if row is None:
            raise LookupError(f"no story for circle {circle_id}")
        steps = done_steps(row)
        expected = STEP_NAMES[len(steps)] if len(steps) < len(STEP_NAMES) else None
        if step != expected:
            raise StoryError(409, f"story step {step!r} is out of order (next is {expected!r})")
        entry = {
            "step": step,
            "label": LABELS[step],
            "ok": True,
            "at": iso(utcnow()),
            "mode": mode,
            "paypal": paypal,
            "ai": ai,
            "summary": summary[:TEXT_LIMIT],
            "result": scrub(result or {}),
            "events": events[-MAX_EVENTS_PER_STEP:],
        }
        steps.append(entry)
        row.steps_json = json.dumps(steps, default=str)
        row.pending_step = row.pending_task_id = None
        row.last_error_json = None
        row.updated_at = utcnow()
        if len(steps) == len(STEP_NAMES):
            row.completed_at = utcnow()
        db.add(row)
    return entry


# --- events -----------------------------------------------------------------------------------------


def scrub(value: Any) -> Any:
    """Recursively drop email addresses and phone numbers from strings (recordings are served publicly)."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _PHONE.sub("<phone>", _EMAIL.sub("<email>", value))[:2000]
    return value


async def mark_step_start(sid: str, circle_id: str, step: str) -> None:
    """Publish the step marker the UI can show and capture() slices the bus history by."""
    await get_event_bus().publish(
        sid,
        {
            "type": MARKER_TYPE,
            "phase": "start",
            "step": step,
            "index": STEP_NAMES.index(step),
            "label": LABELS[step],
            "circle_id": circle_id,
        },
    )


def capture(sid: str, step: str) -> list[dict[str, Any]]:
    """The session's events since the latest start marker of `step` (scrubbed, trace text truncated)."""
    history = get_event_bus().recent(sid, limit=10_000)
    start = 0
    for i in range(len(history) - 1, -1, -1):
        ev = history[i]
        if ev.get("type") == MARKER_TYPE and ev.get("step") == step and ev.get("phase") == "start":
            start = i
            break
    out: list[dict[str, Any]] = []
    for ev in history[start:]:
        e = dict(ev)
        if isinstance(e.get("text"), str):
            e["text"] = e["text"][:TEXT_LIMIT]
        out.append(scrub(e))
    return out


# --- views ------------------------------------------------------------------------------------------


# Opt-in "Story B": steps 6-7 are answered by the members' own agents inside their signed mandates.
VARIANT_B = "member_agents"
B_LABELS: dict[str, str] = {
    "consents": "Member agents answer consents (inside their mandates)",
    "votes": "Member agents vote (inside their mandates)",
}


def story_view(
    circle_id: str, *, live: bool, running: dict[str, Any] | None = None, variant: str | None = None
) -> dict[str, Any]:
    row = load(circle_id)
    done = done_steps(row)
    nxt = next_step(row)
    err = last_error(row)

    def lab(name: str) -> str:
        return B_LABELS[name] if variant == VARIANT_B and name in B_LABELS else label_for(name, live=live)

    if err is not None and err.get("step") != nxt:
        err = None  # stale: that step has since succeeded or the story moved on
    steps = []
    for i, name in enumerate(STEP_NAMES):
        if i < len(done):
            status = "done"
        elif running and running.get("step") == name:
            status = "running"
        elif name == nxt:
            status = "next"
        else:
            status = "todo"
        rec = done[i] if i < len(done) else {}
        steps.append(
            {
                "step": name,
                "label": lab(name),
                "status": status,
                "at": rec.get("at"),
                "mode": rec.get("mode"),
                "paypal": rec.get("paypal"),
                "ai": rec.get("ai"),
                "summary": rec.get("summary"),
                # why the last attempt at this (next) step failed, as text; the full record is `last_error`
                "error": (
                    str(err.get("label") or err.get("code") or "failed")
                    if err is not None and name == nxt and status != "running"
                    else None
                ),
            }
        )
    current = done[-1]["step"] if done else None
    next_view = None
    if nxt is not None:
        next_view = {
            "step": nxt,
            "label": lab(nxt),
            "action_label": ("Running: " if running else "Next: ") + lab(nxt),
            "index": STEP_NAMES.index(nxt),
        }
    view = {
        "ok": True,
        "circle_id": circle_id,
        "run_id": row.run_id if row else None,
        "steps": steps,
        "current": current,
        "next": next_view,
        "running": running,
        "complete": nxt is None,
        "paypal": "live" if live else "simulated",
        "replay_url": REPLAY_URL,
        "last_error": err,
        "tainted": bool(row.tainted) if row else False,
    }
    if variant is not None:  # the key exists only for Story B: the default story's view is unchanged
        view["variant"] = variant
    return view


# --- recordings -------------------------------------------------------------------------------------


KEEP_RECORDINGS_PER_MODE = 5  # older recordings are deleted on save (the table stays small)
PAYPAL_STEPS = ("run_round3", "payout", "clean_round4")  # a recording is "live" only if all of these ran live


def recording_paypal_mode(steps: list[dict[str, Any]]) -> str:
    """'live' only when every money step of the recorded run made real sandbox calls, else 'simulated'."""
    by_step = {s.get("step"): s for s in steps}

    def paypal_of(name: str) -> Any:
        rec = by_step.get(name) or {}
        return rec.get("paypal") if "paypal" in rec else rec.get("mode")

    return "live" if all(paypal_of(name) == "live" for name in PAYPAL_STEPS) else "simulated"


def save_recording(circle_id: str) -> ReplayRecording | None:
    """Turn a completed story into a recording (every step succeeded, in order). It is canonical (served at
    /api/replay) unless Advanced Judge controls were used mid-run; its paypal_mode comes from the steps."""
    row = load(circle_id)
    steps = done_steps(row)
    if row is None or len(steps) != len(STEP_NAMES):
        return None
    paypal_mode = recording_paypal_mode(steps)
    canonical = not row.tainted
    keys = ("step", "label", "at", "mode", "paypal", "ai", "summary", "result", "events")
    payload = {
        "label": REPLAY_LABEL,
        "run_id": row.run_id,
        "paypal_mode": paypal_mode,
        "started_at": iso(row.started_at),
        "completed_at": iso(row.completed_at),
        "steps": [{k: s.get(k) for k in keys} for s in steps],
    }
    with session_scope() as db:
        rec = ReplayRecording(
            circle_id=circle_id,
            run_id=row.run_id,
            canonical=canonical,
            paypal_mode=paypal_mode,
            json=json.dumps(scrub(payload), default=str),
        )
        db.add(rec)
        db.flush()
        _prune(db)
    _REPLAY_CACHE.clear()
    log.info(
        "replay recorded from circle %s (%s, %s)",
        circle_id,
        paypal_mode,
        "canonical" if canonical else "not canonical: Advanced controls were used",
    )
    return rec


def _prune(db: Any) -> None:
    """Keep the newest KEEP_RECORDINGS_PER_MODE canonical recordings per paypal_mode and as many non-canonical
    ones (never served; kept only for debugging); delete the rest."""
    for canonical, mode in ((True, "live"), (True, "simulated"), (False, None)):
        q = select(ReplayRecording.id).where(ReplayRecording.canonical == canonical)
        if mode is not None:
            q = q.where(ReplayRecording.paypal_mode == mode)
        q = q.order_by(ReplayRecording.id.desc()).offset(KEEP_RECORDINGS_PER_MODE)  # type: ignore[union-attr]
        for rid in list(db.exec(q).all()):
            old = db.get(ReplayRecording, rid)
            if old is not None:
                db.delete(old)


# Parsed payload of the newest served recording, keyed by its row id (a new save or a reset DB changes the id).
_REPLAY_CACHE: dict[Any, dict[str, Any]] = {}


def _newest_canonical(prefer_live: bool) -> tuple[Any, ...] | None:
    """(id, created_at) of the one recording /api/replay serves: newest live first (when preferred), then
    newest. The pair is the cache key (an id alone could repeat after a database reset)."""
    order: list[Any] = [ReplayRecording.id.desc()]  # type: ignore[union-attr]
    if prefer_live:
        order.insert(0, case((ReplayRecording.paypal_mode == "live", 0), else_=1))
    with session_scope() as db:
        row = db.exec(
            select(ReplayRecording.id, ReplayRecording.created_at)
            .where(ReplayRecording.canonical == True)  # noqa: E712 - SQL expression
            .order_by(*order)
            .limit(1)
        ).first()
    return tuple(row) if row is not None else None


def replay_available() -> bool:
    """Cheap check for the waitlist body: a canonical recording exists, or the shipped one is there."""
    return _newest_canonical(False) is not None or SHIPPED_REPLAY.is_file()


def latest_recording(*, prefer_live: bool = True) -> dict[str, Any] | None:
    """The last canonical recording (a live-sandbox one first), else the shipped fixture, else None.

    One row id is looked up per call; the parsed payload is cached by that id and each caller gets a copy."""
    hit = _newest_canonical(prefer_live)
    if hit is not None:
        rid = hit[0]
        data = _REPLAY_CACHE.get(hit)
        if data is None:
            with session_scope() as db:
                rec = db.get(ReplayRecording, rid)
                raw, created = (rec.json, rec.created_at) if rec is not None else ("", None)
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                parsed.update({"recorded_at": iso(created) if created else None, "source": "recorded"})
                _REPLAY_CACHE.clear()
                _REPLAY_CACHE[hit] = parsed
                data = parsed
        if data is not None:
            return copy.deepcopy(data)
    if SHIPPED_REPLAY.is_file():
        try:
            data = json.loads(SHIPPED_REPLAY.read_text(encoding="utf-8"))
            data.setdefault("label", REPLAY_LABEL)
            data["source"] = "shipped"
            return scrub(data)
        except (OSError, ValueError) as e:
            log.warning("shipped replay unreadable: %s", type(e).__name__)
    return None
