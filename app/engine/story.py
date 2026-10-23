"""Judge Mode story: one "Next step" button walks the scripted demo (PLAN.md sections 3, 10 and 11).

This module keeps the story's progress per circle (`JudgeStoryRow`), captures the session's events per step from
the SSE bus, and turns a completed story into a read-only `ReplayRecording` (served at GET /api/replay, for
visitors who hit the session cap). The step executors live in app/routes/judge_story.py.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

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
        db.add(row)
        return row


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
) -> dict[str, Any]:
    """Append a finished step (it must be the story's next step) and clear any pending task."""
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
            "summary": summary[:TEXT_LIMIT],
            "result": scrub(result or {}),
            "events": events[-MAX_EVENTS_PER_STEP:],
        }
        steps.append(entry)
        row.steps_json = json.dumps(steps, default=str)
        row.pending_step = row.pending_task_id = None
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


def story_view(circle_id: str, *, live: bool, running: dict[str, Any] | None = None) -> dict[str, Any]:
    row = load(circle_id)
    done = done_steps(row)
    nxt = next_step(row)
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
                "label": label_for(name, live=live),
                "status": status,
                "at": rec.get("at"),
                "mode": rec.get("mode"),
                "summary": rec.get("summary"),
            }
        )
    current = done[-1]["step"] if done else None
    next_view = None
    if nxt is not None:
        next_view = {
            "step": nxt,
            "label": label_for(nxt, live=live),
            "action_label": ("Running: " if running else "Next: ") + label_for(nxt, live=live),
            "index": STEP_NAMES.index(nxt),
        }
    return {
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
    }


# --- recordings -------------------------------------------------------------------------------------


def save_recording(circle_id: str, *, paypal_mode: str) -> ReplayRecording | None:
    """Turn a completed story into a canonical recording (every step succeeded, in order)."""
    row = load(circle_id)
    steps = done_steps(row)
    if row is None or len(steps) != len(STEP_NAMES):
        return None
    payload = {
        "label": REPLAY_LABEL,
        "run_id": row.run_id,
        "paypal_mode": paypal_mode,
        "started_at": iso(row.started_at),
        "completed_at": iso(row.completed_at),
        "steps": [
            {k: s.get(k) for k in ("step", "label", "at", "mode", "summary", "result", "events")}
            for s in steps
        ],
    }
    with session_scope() as db:
        rec = ReplayRecording(
            circle_id=circle_id,
            run_id=row.run_id,
            canonical=True,
            paypal_mode=paypal_mode,
            json=json.dumps(scrub(payload), default=str),
        )
        db.add(rec)
    log.info("canonical replay recorded from circle %s (%s)", circle_id, paypal_mode)
    return rec


def latest_recording(*, prefer_live: bool = True) -> dict[str, Any] | None:
    """The last canonical recording (a live-sandbox one first), else the shipped fixture, else None."""
    with session_scope() as db:
        rows = list(
            db.exec(
                select(ReplayRecording)
                .where(ReplayRecording.canonical == True)  # noqa: E712 - SQL expression
                .order_by(ReplayRecording.id.desc())  # type: ignore[union-attr]
            ).all()
        )
    if prefer_live:
        rows.sort(key=lambda r: r.paypal_mode != "live")  # stable: newest live first, then newest simulated
    for rec in rows:
        try:
            data = json.loads(rec.json)
        except ValueError:
            continue
        data.update({"recorded_at": iso(rec.created_at), "source": "recorded"})
        return data
    if SHIPPED_REPLAY.is_file():
        try:
            data = json.loads(SHIPPED_REPLAY.read_text(encoding="utf-8"))
            data.setdefault("label", REPLAY_LABEL)
            data["source"] = "shipped"
            return scrub(data)
        except (OSError, ValueError) as e:
            log.warning("shipped replay unreadable: %s", type(e).__name__)
    return None
