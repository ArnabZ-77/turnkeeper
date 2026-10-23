"""Forecaster (pure parts): payday rules, the deterministic score, templates, model wording + fallbacks.
Fake providers only; nothing here reaches a network."""

import json
from datetime import date

import pytest

from app.agent import forecast as fc
from app.agent.llm import LLMProvider, LLMTurn, LLMUnavailable

DUE = date(2027, 1, 1)  # round 3 of the demo circle (a Friday)


class FakeProvider(LLMProvider):
    name = "groq"

    def __init__(self, script):
        super().__init__(model="fake-model", live=True)
        self.client = object()
        self.script = list(script)
        self.calls: list[dict] = []

    async def chat(self, system, messages, tools, *, max_tokens):
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def ravi(**kw) -> fc.MemberSignals:
    base = dict(
        member="ravi",
        round_n=3,
        due_date=DUE,
        as_of=date(2026, 12, 29),
        lang="hi",
        payday_rule="every 2nd Friday",
        late_rounds=[1, 2],
        replies=["शुक्रवार को सैलरी आएगी, तब भर दूंगा"],
        track_record_rounds=0,
    )
    base.update(kw)
    return fc.MemberSignals(**base)


def asha(**kw) -> fc.MemberSignals:
    base = dict(
        member="asha",
        round_n=3,
        due_date=DUE,
        as_of=date(2026, 12, 29),
        payday_rule="1st of the month",
        track_record_rounds=6,
    )
    base.update(kw)
    return fc.MemberSignals(**base)


# --- payday rules ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule, kind, paydays",
    [
        ("1st of the month", "month_days", [date(2026, 12, 1), date(2027, 1, 1)]),
        ("every 2nd Friday", "nth_weekday", [date(2026, 12, 11)]),
        ("last working day", "last_working_day", [date(2026, 12, 31)]),
        ("15th and 30th", "month_days", [date(2026, 12, 15), date(2026, 12, 30)]),
        ("every Friday", "weekly", [date(2026, 12, d) for d in (4, 11, 18, 25)] + [date(2027, 1, 1)]),
        ("Fridays", "weekly", [date(2026, 12, d) for d in (4, 11, 18, 25)] + [date(2027, 1, 1)]),
        ("last Friday of the month", "nth_weekday", [date(2026, 12, 25)]),
        ("every other Friday", "uncertain", []),
    ],
)
def test_parse_payday_rule(rule, kind, paydays):
    spec = fc.parse_payday_rule(rule)
    assert spec is not None and spec.kind == kind
    assert fc.paydays_between(spec, date(2026, 12, 1), DUE) == paydays


def test_unreadable_payday_rule_is_none():
    assert fc.parse_payday_rule(None) is None
    assert fc.parse_payday_rule("  ") is None
    assert fc.parse_payday_rule("whenever") is None


def test_month_day_clamps_to_short_months():
    spec = fc.parse_payday_rule("30th")
    assert fc.paydays_between(spec, date(2027, 2, 1), date(2027, 2, 28)) == [date(2027, 2, 28)]


# --- score -------------------------------------------------------------------------------------------------


def test_demo_ravi_is_flagged_with_every_factor():
    res = fc.risk_score(ravi())
    assert res.flagged and res.score == pytest.approx(0.85)
    kinds = {f.factor for f in res.factors}
    assert kinds == {"base", "late_history", "payday", "replies", "track_record"}
    payday = next(f for f in res.factors if f.factor == "payday")
    assert "2026-12-11" in payday.detail and "next 2027-01-08" in payday.detail


def test_reliable_member_is_not_flagged():
    res = fc.risk_score(asha())
    assert not res.flagged and res.score == pytest.approx(0.05)


def test_threshold_is_inclusive_and_configurable():
    # 0.05 base + 0.40 late + 0.05 no track record = 0.50 exactly
    sig = asha(late_rounds=[1, 2], track_record_rounds=0)
    assert fc.risk_score(sig).score == pytest.approx(0.50)
    assert fc.risk_score(sig).flagged
    assert not fc.risk_score(sig, threshold=0.6).flagged


def test_only_earlier_rounds_count_and_late_history_caps_at_two():
    assert fc.risk_score(asha(late_rounds=[3, 4])).score == pytest.approx(0.05)
    one = fc.risk_score(asha(round_n=5, late_rounds=[1])).score
    three = fc.risk_score(asha(round_n=5, late_rounds=[1, 2, 3])).score
    assert one == pytest.approx(0.25) and three == pytest.approx(0.45)


def test_attempts_and_retries_add_risk():
    res = fc.risk_score(asha(attempts_this_round=1, past_retry_rounds=[1, 2]))
    att = next(f for f in res.factors if f.factor == "attempts")
    assert att.weight == pytest.approx(0.30)
    assert "already failed" in att.detail and "retry" in att.detail


def test_payday_stale_unknown_and_uncertain():
    stale = fc.risk_score(asha(payday_rule="15th")).factors  # 17 days before Jan 1
    assert any(f.factor == "payday" and f.weight == fc.W_PAYDAY_STALE for f in stale)
    unknown = fc.risk_score(asha(payday_rule=None)).factors
    assert any(f.factor == "payday" and f.weight == fc.W_PAYDAY_UNKNOWN for f in unknown)
    unsure = fc.risk_score(asha(payday_rule="biweekly on Friday")).factors
    assert any(f.factor == "payday" and f.weight == fc.W_PAYDAY_UNCERTAIN for f in unsure)


def test_reply_keywords_en_and_hi():
    assert fc.reply_signals_delay("Sorry, salary comes next week")
    assert fc.reply_signals_delay("सैलरी आएगी")
    assert not fc.reply_signals_delay("Paid, thanks!")


def test_score_is_deterministic():
    assert fc.risk_score(ravi()).model_dump() == fc.risk_score(ravi()).model_dump()


# --- wording -----------------------------------------------------------------------------------------------


def test_rules_explanation_hi_and_en():
    res = fc.risk_score(ravi())
    ex = fc.rules_explanation(ravi(), res, name="Ravi", amount="$200.00")
    assert ex.mode == "rules"
    assert ex.reason.startswith("ravi at risk (0.85): paid late in rounds 1 and 2")
    assert "नमस्ते Ravi" in ex.nudge and "2027-01-01" in ex.nudge
    assert ex.nudge_en.startswith("Hi Ravi") and "$200.00" in ex.nudge_en
    en = fc.rules_explanation(ravi(lang="en"), res, name="Ravi", amount="$200.00")
    assert en.nudge == en.nudge_en
    other = fc.rules_explanation(ravi(lang="xx"), res, name="Ravi", amount="$200.00")
    assert other.nudge.startswith("Hi Ravi")  # unknown language -> English


def test_prompt_has_no_member_text_or_name():
    prompt = fc.explain_prompt(ravi(), fc.risk_score(ravi()), amount="$200.00")
    assert "सैलरी" not in prompt and "Ravi" not in prompt
    facts = json.loads(prompt.split("\n", 1)[1])
    assert facts["member"] == "ravi" and facts["lang"] == "hi" and facts["score"] == 0.85


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "not json",
        '{"reason": "", "nudge": "x"}',
        '{"reason": "r", "nudge": "pay at https://evil.example"}',
        '{"reason": "r", "nudge": "call +1 555 123 4567"}',
        '{"reason": "r", "nudge": "mail me@example.com"}',
        "[1, 2]",
    ],
)
def test_parse_model_explanation_rejects(text):
    assert fc.parse_model_explanation(text, name="Ravi") is None


def test_parse_model_explanation_fills_name_and_keeps_dates():
    text = '```json\n{"reason": "late twice;\\n payday after due", "nudge": "नमस्ते {name}, 2027-01-01 को $200.00", "nudge_en": "Hi {name}"}\n```'
    ex = fc.parse_model_explanation(text, name="Ravi")
    assert ex.reason == "late twice; payday after due"
    assert ex.nudge == "नमस्ते Ravi, 2027-01-01 को $200.00" and ex.nudge_en == "Hi Ravi"


async def test_explain_uses_the_model_once_with_low_max_tokens():
    reply = json.dumps(
        {
            "reason": "Late twice; payday after the due date.",
            "nudge": "नमस्ते {name}!",
            "nudge_en": "Hi {name}!",
        }
    )
    p = FakeProvider([LLMTurn(text=reply, stop="end")])
    ex = await fc.explain(ravi(), fc.risk_score(ravi()), name="Ravi", amount="$200.00", provider=p)
    assert ex.mode == "live" and ex.nudge == "नमस्ते Ravi!" and ex.nudge_en == "Hi Ravi!"
    assert len(p.calls) == 1 and p.calls[0]["max_tokens"] == fc.FORECAST_MAX_TOKENS <= 400


@pytest.mark.parametrize(
    "step",
    [
        LLMUnavailable("rate_limit", "429"),
        LLMTurn(text="", stop="refusal"),
        LLMTurn(text="sure! here you go", stop="end"),
        LLMTurn(text='{"reason": "x"}', stop="max_tokens"),
    ],
)
async def test_explain_falls_back_to_rules(step):
    p = FakeProvider([step])
    ex = await fc.explain(ravi(), fc.risk_score(ravi()), name="Ravi", amount="$200.00", provider=p)
    assert ex.mode == "rules" and "नमस्ते Ravi" in ex.nudge


async def test_explain_without_any_provider_is_rules():
    # tests/conftest.py forces TK_LLM_PROVIDER=none: no provider, no network
    ex = await fc.explain(ravi(), fc.risk_score(ravi()), name="Ravi", amount="$200.00")
    assert ex.mode == "rules"


async def test_explain_replays_from_the_cache(monkeypatch):
    from sqlmodel import delete

    from app.config import get_settings
    from app.db import session_scope
    from app.models import LlmCache

    monkeypatch.setenv("TK_LLM_CACHE", "auto")
    get_settings.cache_clear()
    with session_scope() as db:
        db.exec(delete(LlmCache))
    reply = json.dumps({"reason": "Late twice.", "nudge": "नमस्ते {name}", "nudge_en": "Hi {name}"})
    first = FakeProvider([LLMTurn(text=reply, stop="end")])
    a = await fc.explain(ravi(), fc.risk_score(ravi()), name="Ravi", amount="$200.00", provider=first)
    second = FakeProvider([])  # any live call would pop from an empty script and fail
    b = await fc.explain(ravi(), fc.risk_score(ravi()), name="Meera", amount="$200.00", provider=second)
    assert (a.mode, b.mode) == ("live", "replay") and b.nudge == "नमस्ते Meera" and second.calls == []
    with session_scope() as db:
        db.exec(delete(LlmCache))
