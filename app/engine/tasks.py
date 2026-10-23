"""Background agent runs for the collect/payout routes and Judge Mode.

`start_task` spawns an asyncio task that calls app.agent.treasurer.run_task (B3, imported lazily).
Events go to the session's SSE stream; every `trace` event is also persisted as an AgentTrace row.
When the agent package is missing, `fallback_engine=True` runs the engine function directly.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.db import session_scope
from app.engine.common import iso, utcnow
from app.engine.events import Publish, get_event_bus
from app.models import AgentTrace

log = logging.getLogger("turnkeeper.engine.tasks")

TRACE_PAYLOAD_LIMIT = 4000


class TaskRecord:
    def __init__(self, task_id: str, kind: str, circle_id: str, round_n: int, sid: str) -> None:
        self.task_id = task_id
        self.kind = kind
        self.circle_id = circle_id
        self.round_n = round_n
        self.sid = sid
        self.started_at: datetime = utcnow()
        self.finished_at: datetime | None = None
        self.done = False
        self.ok: bool | None = None
        self.mode: str | None = None
        self.summary: str | None = None
        self.error: str | None = None
        self.task: asyncio.Task[None] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "circle_id": self.circle_id,
            "round_n": self.round_n,
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "done": self.done,
            "ok": self.ok,
            "mode": self.mode,
            "summary": self.summary,
            "error": self.error,
        }


_TASKS: dict[str, TaskRecord] = {}


def agent_available() -> tuple[bool, str]:
    try:
        mod = importlib.import_module("app.agent.treasurer")
    except ImportError as e:
        return False, f"agent not available: {e}"
    if not hasattr(mod, "run_task") or not hasattr(mod, "AgentTask"):
        return False, "agent module lacks run_task/AgentTask"
    return True, ""


def make_publisher(sid: str, circle_id: str, run_id: str) -> Publish:
    bus = get_event_bus()

    async def _publish(event: dict) -> None:
        await bus.publish(sid, event)
        if event.get("type") == "trace":
            try:
                with session_scope() as db:
                    db.add(
                        AgentTrace(
                            circle_id=circle_id,
                            run_id=run_id,
                            kind=str(event.get("kind") or "text"),
                            payload=json.dumps(event, default=str)[:TRACE_PAYLOAD_LIMIT],
                        )
                    )
            except Exception as e:  # noqa: BLE001 - tracing must never break a run
                log.warning("trace persistence failed: %s", e)

    return _publish


def running_for(circle_id: str) -> TaskRecord | None:
    for rec in _TASKS.values():
        if rec.circle_id == circle_id and not rec.done:
            return rec
    return None


def task_status(task_id: str) -> dict[str, Any] | None:
    rec = _TASKS.get(task_id)
    return rec.as_dict() if rec else None


async def _engine_fallback(kind: str, circle_id: str, round_n: int, publish: Publish) -> tuple[bool, str]:
    rounds = importlib.import_module("app.engine.rounds")
    await publish(
        {
            "type": "trace",
            "kind": "system",
            "text": f"agent unavailable; running {kind} through the engine directly",
        }
    )
    if kind == "payout":
        res = await rounds.pay_round(circle_id, round_n, publish=publish)
        return bool(res.get("ok")), res.get("denied") or res.get("error") or f"payout {res.get('status')}"
    results = await rounds.collect_round(circle_id, round_n, publish=publish)
    ok = all(r.get("status") in ("COMPLETED", "COVERED") for r in results) if results else False
    summary = ", ".join(f"{r.get('member')}={r.get('status')}" for r in results)
    return ok, summary


async def _run(rec: TaskRecord, run_id: str, publish: Publish, fallback_engine: bool) -> None:
    try:
        available, why = agent_available()
        if available:
            agent = importlib.import_module("app.agent.treasurer")
            task = agent.AgentTask(
                kind=rec.kind,
                circle_id=rec.circle_id,
                round_n=rec.round_n,
                sid=rec.sid,
                context={"run_id": run_id},
            )
            result = await agent.run_task(task, publish=publish)
            rec.ok = bool(getattr(result, "ok", False))
            rec.mode = getattr(result, "mode", None)
            rec.summary = getattr(result, "summary", None)
        elif fallback_engine:
            rec.mode = "engine"
            rec.ok, rec.summary = await _engine_fallback(rec.kind, rec.circle_id, rec.round_n, publish)
        else:
            rec.ok = False
            rec.error = why
            await publish({"type": "toast", "level": "error", "text": why})
    except Exception as e:  # noqa: BLE001 - surfaced to the UI, never lost
        log.exception("task %s failed", rec.task_id)
        rec.ok = False
        rec.error = f"{type(e).__name__}: {e}"
        await publish({"type": "toast", "level": "error", "text": f"{rec.kind} failed: {type(e).__name__}"})
        await publish({"type": "trace", "kind": "system", "text": rec.error[:400]})
    finally:
        rec.done = True
        rec.finished_at = utcnow()
        await publish({"type": "board"})


def start_task(
    kind: str,
    circle_id: str,
    round_n: int,
    *,
    sid: str,
    run_id: str,
    fallback_engine: bool = False,
    runner: Callable[..., Awaitable[None]] | None = None,
) -> TaskRecord:
    """Spawn the background run and return its record immediately."""
    rec = TaskRecord(task_id=secrets.token_hex(8), kind=kind, circle_id=circle_id, round_n=round_n, sid=sid)
    publish = make_publisher(sid, circle_id, run_id)
    coro = (runner or _run)(rec, run_id, publish, fallback_engine)
    rec.task = asyncio.get_running_loop().create_task(coro, name=f"tk-{kind}-{circle_id}-{round_n}")
    _TASKS[rec.task_id] = rec
    if len(_TASKS) > 500:  # keep the registry bounded
        for tid in [t for t, r in _TASKS.items() if r.done][:250]:
            _TASKS.pop(tid, None)
    return rec
