"""Forecaster backtest (PLAN.md N6 red-team fix): is the "predicted" badge backed by measured accuracy?

60 seeded synthetic member-round histories: 20 members x rounds 3-5 of a monthly circle, with rounds 1-2 as
history only. Late payers are PLANTED by persona; the outcome of each round is drawn from a ground-truth model
that is deliberately NOT the scorer (it uses the member's hidden persona and true payday gap, plus noise).
Before each round the production scorer ``app.agent.forecast.risk_score`` sees only what the app would see
(late history, payday rule, past replies, retries, track record) and flags at the production threshold.

Deterministic (fixed seed, no clock, no LLM). Writes eval/results_forecast.json.

    .venv/Scripts/python eval/forecast_backtest.py [--seed N] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.forecast import (  # noqa: E402
    HORIZON_DAYS,
    THRESHOLD,
    MemberSignals,
    parse_payday_rule,
    paydays_between,
    risk_score,
)
from app.domain.charter import add_months  # noqa: E402

SEED = 20261112
START = date(2027, 1, 1)
HISTORY_ROUNDS = (1, 2)
FORECAST_ROUNDS = (3, 4, 5)
OUT = Path(__file__).resolve().parent / "results_forecast.json"

# persona -> (count, payday rules to draw from, has track record)
PERSONAS: dict[str, tuple[int, list[str], bool]] = {
    "chronic_late": (4, ["every 2nd Friday", "10th", "every other Friday", "3rd Friday"], False),  # planted
    "payday_gap": (4, ["every 2nd Friday", "15th", "20th", "3rd Friday"], False),
    "occasional": (4, ["1st of the month", "every Friday", "last working day", "1st and 15th"], True),
    "reliable": (8, ["1st of the month", "every Friday", "last working day", "1st and 15th"], True),
}
LATE_REPLIES = [
    "Sorry, salary comes next week",
    "शुक्रवार को सैलरी आएगी, तब भर दूंगा",
    "Can I pay on the 10th? Money is tight",
    "थोड़ी देर हो जाएगी, माफ़ कीजिए",
]
ONTIME_REPLIES = ["Paid, thanks!", "Done 👍", "हो गया"]


def due_of(n: int) -> date:
    return add_months(START, n - 1)


def true_gap(rule: str, hidden_anchor: date, due: date) -> int:
    """Days since the member's real last payday before ``due`` (biweekly rules use a hidden anchor)."""
    spec = parse_payday_rule(rule)
    if spec is None or spec.kind == "uncertain":
        d = hidden_anchor
        while d + timedelta(days=14) <= due:
            d += timedelta(days=14)
        return (due - d).days
    days = paydays_between(spec, due - timedelta(days=45), due)
    return (due - days[-1]).days if days else 45


def p_late(persona: str, gap: int) -> float:
    """Ground truth (hidden from the scorer): persona base rate, worse when pay is stale at the due date."""
    if persona == "chronic_late":
        return 0.70
    if persona == "payday_gap":
        return 0.55 if gap > 20 else 0.15
    if persona == "occasional":
        return 0.20
    return 0.04


def build_members(rng: random.Random) -> list[dict[str, Any]]:
    members = []
    for persona, (count, rules, tracked) in PERSONAS.items():
        for i in range(count):
            members.append(
                {
                    "member": f"{persona[:4]}{i + 1}",
                    "persona": persona,
                    "planted": persona == "chronic_late",
                    "rule": rules[i % len(rules)],
                    "lang": rng.choice(["en", "hi"]),
                    "track_record_rounds": rng.choice([3, 6]) if tracked else 0,
                    # a Friday in the four weeks before the first due date
                    "anchor": date(2026, 12, 4) + timedelta(days=7 * rng.randrange(4)),
                    "late": [],
                    "retries": [],
                    "replies": [],
                }
            )
    return members


def play_round(rng: random.Random, m: dict[str, Any], n: int) -> bool:
    due = due_of(n)
    late = rng.random() < p_late(m["persona"], true_gap(m["rule"], m["anchor"], due))
    if late:
        m["late"].append(n)
        if rng.random() < 0.5:
            m["retries"].append(n)
        if rng.random() < 0.6:
            m["replies"].append(rng.choice(LATE_REPLIES))
    elif rng.random() < 0.1:
        m["replies"].append(rng.choice(ONTIME_REPLIES))
    return late


def ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def confusion(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    tp = sum(1 for r in rows if r["score"] >= threshold and r["late"])
    fp = sum(1 for r in rows if r["score"] >= threshold and not r["late"])
    fn = sum(1 for r in rows if r["score"] < threshold and r["late"])
    tn = len(rows) - tp - fp - fn
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 3)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "threshold": threshold,
        "hits": tp,
        "misses": fn,
        "false_alarms": fp,
        "correct_rejections": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": ratio(tp + tn, len(rows)),
    }


def run(seed: int = SEED) -> dict[str, Any]:
    rng = random.Random(seed)
    members = build_members(rng)
    for n in HISTORY_ROUNDS:
        for m in members:
            play_round(rng, m, n)
    rows: list[dict[str, Any]] = []
    for n in FORECAST_ROUNDS:
        due = due_of(n)
        for m in members:
            sig = MemberSignals(
                member=m["member"],
                round_n=n,
                due_date=due,
                as_of=due - timedelta(days=HORIZON_DAYS),
                lang=m["lang"],
                payday_rule=m["rule"],
                late_rounds=list(m["late"]),
                past_retry_rounds=list(m["retries"]),
                replies=list(m["replies"]),
                track_record_rounds=m["track_record_rounds"],
            )
            res = risk_score(sig)
            late = play_round(rng, m, n)  # the outcome is drawn AFTER the forecast
            rows.append(
                {
                    "member": m["member"],
                    "persona": m["persona"],
                    "planted": m["planted"],
                    "round_n": n,
                    "score": res.score,
                    "flagged": res.flagged,
                    "late": late,
                }
            )
    main = confusion(rows, THRESHOLD)
    planted = [r for r in rows if r["planted"]]
    by_persona = {}
    for persona in PERSONAS:
        sub = [r for r in rows if r["persona"] == persona]
        by_persona[persona] = {
            "member_rounds": len(sub),
            "late": sum(r["late"] for r in sub),
            "flagged": sum(r["flagged"] for r in sub),
            "hits": sum(r["late"] and r["flagged"] for r in sub),
        }
    return {
        "name": "forecaster backtest",
        "seed": seed,
        "member_rounds": len(rows),
        "members": len(members),
        "forecast_rounds": list(FORECAST_ROUNDS),
        "horizon_days": HORIZON_DAYS,
        "late_base_rate": ratio(sum(r["late"] for r in rows), len(rows)),
        **main,
        "planted_late_payers": {
            "members": sum(1 for m in members if m["planted"]),
            "member_rounds": len(planted),
            "late": sum(r["late"] for r in planted),
            "flagged": sum(r["flagged"] for r in planted),
            "late_and_flagged": sum(r["late"] and r["flagged"] for r in planted),
        },
        "by_persona": by_persona,
        "threshold_sweep": [confusion(rows, t) for t in (0.3, 0.4, 0.5, 0.6, 0.7)],
        "misses_detail": [r for r in rows if r["late"] and not r["flagged"]],
        "false_alarms_detail": [r for r in rows if r["flagged"] and not r["late"]],
        "method": (
            "Synthetic members with hidden personas (chronic_late = planted, payday_gap, occasional, reliable). "
            "Outcomes come from a ground-truth model the scorer never sees (persona base rate, true payday gap "
            "with a hidden biweekly anchor, randomness); the production risk_score reads only app-visible "
            "signals. No LLM is involved: the score is rules; a model only words the reason and nudge."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    result = run(args.seed)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"forecast backtest: {result['member_rounds']} member-rounds, hits {result['hits']}, misses "
        f"{result['misses']}, false alarms {result['false_alarms']}, precision {result['precision']}, "
        f"recall {result['recall']} -> {args.out.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
