"""Shortfall forecaster: a deterministic risk score per member before a due date, plus a one-line reason
and a nudge for flagged members (PLAN.md section 3 step 6).

The score is pure rules over a ``MemberSignals`` snapshot, so it is reproducible and backtested
(``eval/forecast_backtest.py`` uses ``risk_score`` unchanged). Only the wording of a flagged member's reason and
nudge may come from a model: one tool-free turn through ``app.agent.llm.complete_text`` (provider layer +
replay cache, low ``max_tokens``). Anything else (no key, quota spent, refusal, a reply that is not the JSON we
asked for, a link in the text) uses the en/hi templates below and is labelled ``mode="rules"``.

Member-written replies are DATA: they only feed a keyword check here and are never put into a prompt.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger("turnkeeper.agent.forecast")

THRESHOLD = 0.5
HORIZON_DAYS = 3  # the forecast runs at T-3 before each due date
FORECAST_MAX_TOKENS = 400  # one short JSON object; reasoning models spend part of it thinking
REASON_MAX_CHARS = 200
NUDGE_MAX_CHARS = 280
NAME_PLACEHOLDER = "{name}"

# Weights (documented in docs/CONTRACTS.md, "Forecaster"). The score is clamped to [0, 0.99].
W_BASE = 0.05
W_LATE_PER_ROUND = 0.20  # up to 2 late rounds count
W_PAYDAY_FAR = 0.25  # last payday on/before the due date is more than 20 days earlier (or none known in 31)
W_PAYDAY_STALE = 0.10  # last payday 8-20 days before the due date
W_PAYDAY_UNKNOWN = 0.05  # no payday rule on file
W_PAYDAY_UNCERTAIN = 0.10  # biweekly with no anchor date
W_REPLY_DELAY = 0.10  # a past reply talks about paying later / waiting for salary
W_CURRENT_ATTEMPT = 0.20  # a charge for this round already failed
W_PAST_RETRY = 0.05  # per earlier round that needed a retry (max 2)
W_NO_TRACK_RECORD = 0.05

Mode = Literal["live", "replay", "rules"]


class MemberSignals(BaseModel):
    """Everything the score reads, for one member and one upcoming round. No PayPal ids, no free-text names."""

    member: str
    round_n: int = Field(ge=1)
    due_date: date
    as_of: date  # the virtual "today" of the forecast (normally due_date - 3 days)
    lang: str = "en"
    payday_rule: str | None = None
    late_rounds: list[int] = Field(default_factory=list)  # earlier rounds only (< round_n)
    past_retry_rounds: list[int] = Field(default_factory=list)  # earlier rounds with attempt > 1
    attempts_this_round: int = 0  # failed attempts already made for round_n
    replies: list[str] = Field(default_factory=list)  # raw member text: keyword-checked, never prompted
    track_record_rounds: int = 0


class Factor(BaseModel):
    factor: Literal["base", "late_history", "payday", "replies", "attempts", "track_record"]
    weight: float
    detail: str


class RiskResult(BaseModel):
    member: str
    round_n: int
    score: float
    flagged: bool
    factors: list[Factor]


class Explanation(BaseModel):
    reason: str
    nudge: str
    nudge_en: str | None = None
    mode: Mode = "rules"


# --- payday rules -----------------------------------------------------------------------------------------

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
    "सोमवार": 0, "मंगलवार": 1, "बुधवार": 2, "गुरुवार": 3, "शुक्रवार": 4, "शनिवार": 5, "रविवार": 6,
}  # fmt: skip
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "last": -1}


class PaydaySpec(BaseModel):
    kind: Literal["month_days", "nth_weekday", "weekly", "last_working_day", "uncertain"]
    days: list[int] = Field(default_factory=list)  # month_days
    weekday: int | None = None  # nth_weekday, weekly
    nth: int | None = None  # nth_weekday (-1 = last)
    note: str = ""


def _weekday_in(text: str) -> int | None:
    for word in re.findall(r"[\wऀ-ॿ]+", text):
        w = word.lower().rstrip("s") if word.isascii() else word
        if w in _WEEKDAYS:
            return _WEEKDAYS[w]
        if word.lower() in _WEEKDAYS:
            return _WEEKDAYS[word.lower()]
    return None


def parse_payday_rule(rule: str | None) -> PaydaySpec | None:
    """Read a free-text payday rule. None = nothing on file or unreadable.

    "every 2nd Friday" is read as the 2nd Friday of each month; "every other Friday", "biweekly" and
    "fortnightly" have no anchor date and come back as ``uncertain``.
    """
    if not rule or not rule.strip():
        return None
    text = rule.strip().lower()
    weekday = _weekday_in(text)
    if re.search(r"every other|bi-?weekly|fortnight", text):
        return PaydaySpec(kind="uncertain", weekday=weekday, note="biweekly, no anchor date")
    if re.search(r"last (working|business|week) ?day|month[- ]end|end of (the )?month", text):
        return PaydaySpec(kind="last_working_day")
    if weekday is not None:
        m = re.search(r"\b(\d)(?:st|nd|rd|th)\b", text)
        nth = int(m.group(1)) if m else None
        if nth is None:
            for word, n in _ORDINAL_WORDS.items():
                if re.search(rf"\b{word}\b", text):
                    nth = n
                    break
        if nth is not None and (nth == -1 or 1 <= nth <= 5):
            return PaydaySpec(kind="nth_weekday", weekday=weekday, nth=nth)
        return PaydaySpec(kind="weekly", weekday=weekday)
    days = [int(d) for d in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", text) if 1 <= int(d) <= 31]
    if days:
        return PaydaySpec(kind="month_days", days=sorted(set(days)))
    return None


def _month_paydays(spec: PaydaySpec, year: int, month: int) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    if spec.kind == "month_days":
        return [date(year, month, min(d, last)) for d in spec.days]
    if spec.kind == "last_working_day":
        d = date(year, month, last)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return [d]
    if spec.kind == "weekly" and spec.weekday is not None:
        first = date(year, month, 1)
        d = first + timedelta(days=(spec.weekday - first.weekday()) % 7)
        out = []
        while d.month == month:
            out.append(d)
            d += timedelta(days=7)
        return out
    if spec.kind == "nth_weekday" and spec.weekday is not None and spec.nth is not None:
        hits = [
            date(year, month, day)
            for day in range(1, last + 1)
            if date(year, month, day).weekday() == spec.weekday
        ]
        if spec.nth == -1:
            return hits[-1:]
        return [hits[spec.nth - 1]] if spec.nth <= len(hits) else []
    return []


def paydays_between(spec: PaydaySpec, start: date, end: date) -> list[date]:
    """Paydays in [start, end] (both inclusive). Empty for an ``uncertain`` spec."""
    if spec.kind == "uncertain" or end < start:
        return []
    out: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.extend(d for d in _month_paydays(spec, y, m) if start <= d <= end)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(out)


# --- replies -----------------------------------------------------------------------------------------------

_DELAY_WORDS = (
    "late", "later", "next week", "salary", "payday", "paycheck", "can't", "cannot", "cant", "short", "tight",
    "wait", "delay", "after", "sorry", "tomorrow",
    "सैलरी", "तनख्वाह", "वेतन", "बाद", "देर", "नहीं", "कल", "अगले", "भर दूंगा",
    "baad", "der", "nahi", "salary aayegi", "agle",
)  # fmt: skip


def reply_signals_delay(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in _DELAY_WORDS)


# --- the score ---------------------------------------------------------------------------------------------


def _fmt_rounds(rounds: list[int]) -> str:
    rs = [str(r) for r in sorted(rounds)]
    return rs[0] if len(rs) == 1 else ", ".join(rs[:-1]) + " and " + rs[-1]


def _payday_factor(signals: MemberSignals) -> Factor | None:
    spec = parse_payday_rule(signals.payday_rule)
    due = signals.due_date
    if spec is None:
        return Factor(factor="payday", weight=W_PAYDAY_UNKNOWN, detail="no payday rule on file")
    if spec.kind == "uncertain":
        return Factor(
            factor="payday",
            weight=W_PAYDAY_UNCERTAIN,
            detail=f"payday '{signals.payday_rule}' is biweekly with no anchor date",
        )
    before = paydays_between(spec, due - timedelta(days=31), due)
    after = paydays_between(spec, due + timedelta(days=1), due + timedelta(days=45))
    nxt = f", next {after[0].isoformat()}" if after else ""
    if not before:
        return Factor(
            factor="payday",
            weight=W_PAYDAY_FAR,
            detail=f"no payday in the 31 days before the {due.isoformat()} due date{nxt}",
        )
    gap = (due - before[-1]).days
    if gap > 20:
        return Factor(
            factor="payday",
            weight=W_PAYDAY_FAR,
            detail=f"last payday {before[-1].isoformat()} is {gap} days before the {due.isoformat()} due date{nxt}",
        )
    if gap > 7:
        return Factor(
            factor="payday",
            weight=W_PAYDAY_STALE,
            detail=f"last payday {before[-1].isoformat()} is {gap} days before the due date",
        )
    return None


def risk_score(signals: MemberSignals, *, threshold: float = THRESHOLD) -> RiskResult:
    """Deterministic shortfall risk for one member before ``signals.due_date``. Flagged when >= threshold."""
    factors = [Factor(factor="base", weight=W_BASE, detail="base rate")]
    late = sorted({r for r in signals.late_rounds if r < signals.round_n})
    if late:
        factors.append(
            Factor(
                factor="late_history",
                weight=W_LATE_PER_ROUND * min(len(late), 2),
                detail=f"paid late in round{'s' if len(late) > 1 else ''} {_fmt_rounds(late)}",
            )
        )
    payday = _payday_factor(signals)
    if payday is not None:
        factors.append(payday)
    delay_replies = [r for r in signals.replies if reply_signals_delay(r)]
    if delay_replies:
        factors.append(
            Factor(
                factor="replies",
                weight=W_REPLY_DELAY,
                detail=f"{len(delay_replies)} earlier repl{'ies' if len(delay_replies) > 1 else 'y'} asked for more time",
            )
        )
    attempt_weight = 0.0
    attempt_bits: list[str] = []
    if signals.attempts_this_round > 0:
        attempt_weight += W_CURRENT_ATTEMPT
        attempt_bits.append(f"a round {signals.round_n} charge already failed")
    retries = sorted({r for r in signals.past_retry_rounds if r < signals.round_n})
    if retries:
        attempt_weight += W_PAST_RETRY * min(len(retries), 2)
        attempt_bits.append(
            f"needed a retry in round{'s' if len(retries) > 1 else ''} {_fmt_rounds(retries)}"
        )
    if attempt_weight:
        factors.append(Factor(factor="attempts", weight=attempt_weight, detail="; ".join(attempt_bits)))
    if signals.track_record_rounds <= 0:
        factors.append(
            Factor(
                factor="track_record", weight=W_NO_TRACK_RECORD, detail="no track record in earlier circles"
            )
        )
    total = sum(Decimal(str(f.weight)) for f in factors)
    score = float(min(max(total, Decimal("0")), Decimal("0.99")).quantize(Decimal("0.01")))
    return RiskResult(
        member=signals.member,
        round_n=signals.round_n,
        score=score,
        flagged=score >= threshold,
        factors=factors,
    )


# --- wording -----------------------------------------------------------------------------------------------

_NUDGE_TEMPLATES = {
    "en": (
        "Hi {name}, a friendly reminder: your {amount} for round {round_n} is due {due}. If your pay comes "
        "later, reply with the date and the circle can plan for it."
    ),
    "hi": (
        "नमस्ते {name}, याद दिला दें: राउंड {round_n} के {amount} {due} को देय हैं। अगर सैलरी बाद में आएगी, "
        "तो तारीख बता दें, सर्कल पहले से इंतज़ाम कर लेगा।"
    ),
}


def rules_reason(result: RiskResult) -> str:
    """One line in English from the factors that carry weight (the base rate is left out)."""
    parts = [f.detail for f in sorted(result.factors, key=lambda f: -f.weight) if f.factor != "base"]
    head = (
        f"{result.member} at risk ({result.score:.2f})"
        if result.flagged
        else f"{result.member} ({result.score:.2f})"
    )
    return (head + (": " + "; ".join(parts[:3]) if parts else ": no risk factors")).strip()[:REASON_MAX_CHARS]


def rules_nudge(lang: str, *, name: str, amount: str, round_n: int, due: date) -> tuple[str, str]:
    """(nudge in the member's language, English text). Unknown languages get English."""
    fields = {"name": name, "amount": amount, "round_n": round_n, "due": due.isoformat()}
    en = _NUDGE_TEMPLATES["en"].format(**fields)
    return _NUDGE_TEMPLATES.get(lang, _NUDGE_TEMPLATES["en"]).format(**fields), en


def rules_explanation(signals: MemberSignals, result: RiskResult, *, name: str, amount: str) -> Explanation:
    nudge, en = rules_nudge(
        signals.lang, name=name, amount=amount, round_n=signals.round_n, due=signals.due_date
    )
    return Explanation(reason=rules_reason(result), nudge=nudge, nudge_en=en, mode="rules")


SYSTEM_PROMPT = (
    "You write short notes for the treasurer of a rotating savings circle. A deterministic score has flagged "
    "a member who may pay late. Reply with ONE JSON object and nothing else: "
    '{"reason": "...", "nudge": "...", "nudge_en": "..."}. '
    "reason: English, one line, at most 140 characters, based only on the factors given. "
    "nudge: a warm, non-threatening reminder to the member in the language given by 'lang' (hi = Hindi in "
    "Devanagari), one or two sentences, at most 200 characters; address the member as {name} literally; "
    "mention only the amount and due date given; no links, no phone numbers, no other amounts; invite them to "
    "reply with their pay date if it comes later. nudge_en: the English translation of nudge."
)


def explain_prompt(signals: MemberSignals, result: RiskResult, *, amount: str) -> str:
    """The user turn. Logical handle, numbers and factor details only: no names, no member-written text, so the
    replay cache key is the same for every clone of the demo circle."""
    facts = {
        "member": signals.member,
        "lang": signals.lang,
        "round": signals.round_n,
        "due_date": signals.due_date.isoformat(),
        "amount": amount,
        "score": result.score,
        "threshold": THRESHOLD,
        "factors": [{"factor": f.factor, "weight": f.weight, "detail": f.detail} for f in result.factors],
    }
    return "Facts (JSON):\n" + json.dumps(facts, ensure_ascii=False, sort_keys=True)


_JSON_RE = re.compile(r"\{.*\}", re.S)
# links, emails and phone numbers (+E.164, US 555-555-5555, or a bare 10+ digit run); ISO dates pass
_BANNED = re.compile(r"https?://|www\.|@|\+\d[\d\s-]{7,}\d|\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b|\b\d{10,}\b")


def _clean_line(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    line = " ".join(value.split())
    return line[:limit] if line else None


def parse_model_explanation(text: str | None, *, name: str) -> Explanation | None:
    """The model's JSON as an Explanation, or None when it is not usable (callers fall back to rules)."""
    if not text:
        return None
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    reason = _clean_line(data.get("reason"), REASON_MAX_CHARS)
    nudge = _clean_line(data.get("nudge"), NUDGE_MAX_CHARS)
    nudge_en = _clean_line(data.get("nudge_en"), NUDGE_MAX_CHARS)
    if not reason or not nudge:
        return None
    if any(_BANNED.search(t) for t in (reason, nudge, nudge_en or "")):
        return None
    fill = lambda t: t.replace(NAME_PLACEHOLDER, name) if t else t  # noqa: E731
    return Explanation(reason=reason, nudge=fill(nudge), nudge_en=fill(nudge_en))


async def explain(
    signals: MemberSignals,
    result: RiskResult,
    *,
    name: str,
    amount: str,
    provider: Any = None,
    live_allowed: bool = True,
) -> Explanation:
    """Reason + nudge for a flagged member: model wording when one answers (live or replayed), else templates."""
    try:
        from app.agent.llm import complete_text

        text, mode = await complete_text(
            SYSTEM_PROMPT,
            explain_prompt(signals, result, amount=amount),
            max_tokens=FORECAST_MAX_TOKENS,
            provider=provider,
            live_allowed=live_allowed,
        )
    except Exception as exc:  # noqa: BLE001 - the forecast must never fail because of the model
        log.info("forecast explanation unavailable: %s", type(exc).__name__)
        text, mode = None, "rules"
    parsed = parse_model_explanation(text, name=name) if mode in ("live", "replay") else None
    if parsed is None:
        return rules_explanation(signals, result, name=name, amount=amount)
    parsed.mode = mode  # type: ignore[assignment]
    return parsed
