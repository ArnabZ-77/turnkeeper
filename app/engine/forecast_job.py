"""T-3 forecast job on the virtual clock (PLAN.md section 3 step 6).

When the judge advances a circle's clock into the window [due_at - 3 days, due_at) of an unpaid round that has
no forecast yet, ``on_clock_advanced`` scores every member (``app.agent.forecast.risk_score``), asks the model
for a reason + nudge for each FLAGGED member only (replay-cached, rules templates otherwise), and then:

- stores one ``ForecastRow`` per member (``BoardModel.forecasts``),
- sets ``Contribution.predicted`` on that round's PENDING contributions (flagged -> True, else False),
- sends the nudge to the member's inbox (direction out, ``round_n=None`` so it never suppresses the round's
  decline notice, which dedupes on round),
- publishes ``forecast`` (one per flagged member), ``chip`` (each changed contribution), ``trace`` and ``board``.

A round is forecast once (re-running needs ``force=True``), so repeated clock clicks never spend model quota.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from app.agent import forecast as fc
from app.db import session_scope
from app.engine import state
from app.engine.common import as_utc, iso, money_str, utcnow
from app.models import Contribution, ForecastRow, MemberRow, Round

log = logging.getLogger("turnkeeper.engine.forecast_job")

Publish = Callable[[dict[str, Any]], Awaitable[None]]
OPEN_ROUND_STATUSES = frozenset({"PENDING", "COLLECTING"})
MAX_MODEL_EXPLANATIONS = 3  # per run; more flagged members than this get the rules templates (quota)
_locks: dict[str, asyncio.Lock] = {}


def _lock(circle_id: str) -> asyncio.Lock:
    lock = _locks.get(circle_id)
    if lock is None:
        lock = _locks[circle_id] = asyncio.Lock()
    return lock


# --- reads ---------------------------------------------------------------------------------------------


def has_forecast(circle_id: str, round_n: int) -> bool:
    with session_scope() as db:
        row = db.exec(
            select(ForecastRow.id).where(ForecastRow.circle_id == circle_id, ForecastRow.round_n == round_n)
        ).first()
    return row is not None


def due_rounds(circle_id: str, now: datetime | None = None) -> list[int]:
    """Unpaid rounds whose T-3 window [due_at - 3 d, due_at) contains ``now`` and that have no forecast yet."""
    now = as_utc(now) if now is not None else as_utc(state.load_circle(circle_id).virtual_now)
    horizon = timedelta(days=fc.HORIZON_DAYS)
    with session_scope() as db:
        rounds = list(db.exec(select(Round).where(Round.circle_id == circle_id).order_by(Round.n)).all())
    out = []
    for r in rounds:
        due = as_utc(r.due_at)
        if (
            r.status in OPEN_ROUND_STATUSES
            and due - horizon <= now < due
            and not has_forecast(circle_id, r.n)
        ):
            out.append(r.n)
    return out


def member_signals(circle_id: str, round_n: int, member: MemberRow, *, as_of: datetime) -> fc.MemberSignals:
    """The forecaster's input for one member, from contributions, the fixture history and the inbox."""
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        raise LookupError(f"round {round_n} not found")
    with session_scope() as db:
        rows = list(
            db.exec(
                select(Contribution).where(
                    Contribution.circle_id == circle_id, Contribution.member_handle == member.handle
                )
            ).all()
        )
    late: set[int] = set()
    retries: set[int] = set()
    attempts_now = 0
    for c in rows:
        if c.round_n == round_n:
            if c.status == "DECLINED":
                attempts_now = max(c.attempt, 1)
            continue
        if c.round_n > round_n:
            continue
        if c.attempt > 1:
            retries.add(c.round_n)
        if (
            c.status in ("DECLINED", "COVERED")
            or c.attempt > 1
            or (c.label or "").lower().startswith("captured late")
        ):
            late.add(c.round_n)
    replies: list[str] = []
    try:
        hist = state.member_history(circle_id, member.handle)
        late.update(int(n) for n in hist.get("late_rounds", []) if int(n) < round_n)
        replies = [str(r.get("text", "")) for r in hist.get("replies", []) if isinstance(r, dict)]
    except Exception as e:  # noqa: BLE001 - history enrichment is best effort
        log.info("member history unavailable for %s: %s", member.handle, type(e).__name__)
    return fc.MemberSignals(
        member=member.handle,
        round_n=round_n,
        due_date=as_utc(rnd.due_at).date(),
        as_of=as_utc(as_of).date(),
        lang=member.lang or "en",
        payday_rule=member.payday_rule,
        late_rounds=sorted(late),
        past_retry_rounds=sorted(retries),
        attempts_this_round=attempts_now,
        replies=replies,
        track_record_rounds=member.track_record_rounds,
    )


def _row_view(row: ForecastRow) -> dict[str, Any]:
    try:
        factors = json.loads(row.factors_json or "[]")
    except ValueError:
        factors = []
    return {
        "round_n": row.round_n,
        "member": row.member,
        "score": float(row.score),
        "flagged": row.flagged,
        "reason": row.reason,
        "nudge": row.nudge,
        "nudge_en": row.nudge_en,
        "lang": row.lang,
        "mode": row.mode,
        "factors": factors,
        "as_of": iso(row.as_of),
    }


def forecasts_view(circle_id: str) -> list[dict[str, Any]]:
    """BoardModel.forecasts: every stored forecast, latest round first, flagged members first, by score."""
    with session_scope() as db:
        rows = list(db.exec(select(ForecastRow).where(ForecastRow.circle_id == circle_id)).all())
    rows.sort(key=lambda r: (-r.round_n, not r.flagged, -float(r.score), r.member))
    return [_row_view(r) for r in rows]


# --- the job -----------------------------------------------------------------------------------------------


async def _emit(publish: Publish | None, event: dict[str, Any]) -> None:
    if publish is None:
        return
    try:
        await publish(event)
    except Exception as e:  # noqa: BLE001 - a dead SSE stream must not break the forecast
        log.info("forecast publish failed: %s", type(e).__name__)


async def run_forecast(
    circle_id: str,
    round_n: int,
    *,
    publish: Publish | None = None,
    live_allowed: bool = True,
    force: bool = False,
    provider: Any = None,
) -> list[dict[str, Any]]:
    """Forecast one round for every member. Returns the stored forecasts (BoardModel.forecasts shape)."""
    async with _lock(circle_id):
        if has_forecast(circle_id, round_n) and not force:
            return [f for f in forecasts_view(circle_id) if f["round_n"] == round_n]
        circle = state.load_circle(circle_id)
        charter = state.charter_of(circle)
        amount = f"${money_str(charter.amount)}"
        as_of = as_utc(circle.virtual_now)
        members = state.members_of(circle_id)

        scored: list[tuple[MemberRow, fc.MemberSignals, fc.RiskResult]] = []
        for m in members:
            sig = member_signals(circle_id, round_n, m, as_of=as_of)
            scored.append((m, sig, fc.risk_score(sig)))

        explanations: dict[str, fc.Explanation] = {}
        model_budget = MAX_MODEL_EXPLANATIONS
        for m, sig, res in sorted(scored, key=lambda t: -t[2].score):
            if not res.flagged:
                continue
            name = (m.name or m.handle).split()[0]
            if model_budget > 0:
                model_budget -= 1
                explanations[m.handle] = await fc.explain(
                    sig, res, name=name, amount=amount, provider=provider, live_allowed=live_allowed
                )
            else:
                explanations[m.handle] = fc.rules_explanation(sig, res, name=name, amount=amount)

        changed: list[Contribution] = []
        with session_scope() as db:
            if force:
                for old in db.exec(
                    select(ForecastRow).where(
                        ForecastRow.circle_id == circle_id, ForecastRow.round_n == round_n
                    )
                ).all():
                    db.delete(old)
            for m, sig, res in scored:
                ex = explanations.get(m.handle)
                db.add(
                    ForecastRow(
                        circle_id=circle_id,
                        round_n=round_n,
                        member=m.handle,
                        score=f"{res.score:.2f}",
                        flagged=res.flagged,
                        reason=ex.reason
                        if ex
                        else fc.rules_reason(res, name=(m.name or m.handle).split()[0]),
                        nudge=ex.nudge if ex else None,
                        nudge_en=ex.nudge_en if ex else None,
                        lang=sig.lang,
                        mode=ex.mode if ex else "rules",
                        factors_json=json.dumps([f.model_dump() for f in res.factors], ensure_ascii=False),
                        as_of=as_of,
                        ts=utcnow(),
                    )
                )
            flagged = {m.handle for m, _, res in scored if res.flagged}
            for c in db.exec(
                select(Contribution).where(
                    Contribution.circle_id == circle_id, Contribution.round_n == round_n
                )
            ).all():
                if c.status != "PENDING":
                    continue
                want = c.member_handle in flagged
                if c.predicted != want:
                    c.predicted = want
                    c.updated_at = utcnow()
                    db.add(c)
                    changed.append(c)

        for m, sig, _res in scored:
            ex = explanations.get(m.handle)
            if ex is None:
                continue
            try:
                from app.engine import inbox

                sent = inbox.list_messages(circle_id, member=m.handle, direction="out", limit=50)
                if any(msg.get("text") == ex.nudge for msg in sent):
                    continue  # a forced re-run never sends the same nudge twice
                inbox.add_message(
                    circle_id,
                    m.handle,
                    ex.nudge,
                    lang=sig.lang,
                    direction="out",
                    source="rules" if ex.mode == "rules" else "agent",
                    round_n=None,
                )
            except Exception as e:  # noqa: BLE001 - the nudge is best effort
                log.info("forecast nudge not stored: %s", type(e).__name__)

        for c in changed:
            await _emit(
                publish,
                {
                    "type": "chip",
                    "round_n": c.round_n,
                    "member": c.member_handle,
                    "status": c.status,
                    "predicted": c.predicted,
                    "label": c.label,
                },
            )
        views = [f for f in forecasts_view(circle_id) if f["round_n"] == round_n]
        for f in views:
            if f["flagged"]:
                await _emit(
                    publish,
                    {
                        "type": "forecast",
                        "round_n": round_n,
                        "member": f["member"],
                        "score": f["score"],
                        "reason": f["reason"],
                        "nudge": f["nudge"],
                        "nudge_en": f["nudge_en"],
                        "lang": f["lang"],
                        "mode": f["mode"],
                    },
                )
        flagged_text = (
            ", ".join(f"{f['member']} ({f['score']:.2f})" for f in views if f["flagged"]) or "nobody"
        )
        modes = sorted({f["mode"] for f in views if f["flagged"]}) or ["rules"]
        await _emit(
            publish,
            {
                "type": "trace",
                "kind": "system",
                "source": "forecast",
                "mode": modes[0] if len(modes) == 1 else "rules",
                "text": f"T-{fc.HORIZON_DAYS} forecast for round {round_n}: flagged {flagged_text} "
                f"(threshold {fc.THRESHOLD}; score = rules, wording = {'/'.join(modes)})",
            },
        )
        await _emit(publish, {"type": "board"})
        log.info("forecast round %s for %s: %d flagged", round_n, circle_id, len(flagged))
        return views


async def on_clock_advanced(
    circle_id: str, *, publish: Publish | None = None, live_allowed: bool = True, provider: Any = None
) -> list[dict[str, Any]]:
    """Run the forecast for every round that just entered its T-3 window. Never raises."""
    out: list[dict[str, Any]] = []
    try:
        for n in due_rounds(circle_id):
            out.extend(
                await run_forecast(
                    circle_id, n, publish=publish, live_allowed=live_allowed, provider=provider
                )
            )
    except Exception as e:  # noqa: BLE001 - a clock click must never fail because of the forecast
        log.warning("forecast job failed for %s: %s", circle_id, e)
    return out
