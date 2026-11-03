"""Export recorded AI turns (the ``llm_cache`` table) to ``app/fixtures/llm_cache.jsonl`` so they ship with the app.

Record once with a free key (Groq or Gemini; ``TK_LLM_CACHE=auto`` or ``record``), click through the demo,
then run:

    .venv/Scripts/python -I scripts/export_llm_cache.py [--out app/fixtures/llm_cache.jsonl] [--dry-run]

The app loads the file at startup (insert-if-missing), and a deploy with no key replays the recording's own
provider and model (``app.agent.llm.replay_identity``). Rows that look like they carry an email address, an API
key or a raw PayPal id are skipped and reported: look at them before shipping anything.
Output: one JSON line per skipped row, then a summary line and PASS/FAIL (exit 0/1).
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent.llm import SHIPPED_CACHE_PATH

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_RE = re.compile(
    r"\b(sk-ant-[\w-]+|sk-[\w-]{16,}|gsk_\w{16,}|AIza[\w-]{20,}|Bearer\s+[\w.-]{16,}|A21AA[\w-]{20,})"
)
# Raw PayPal order/capture/batch/item ids: 13-20 upper-case alphanumerics with at least one digit and letter.
PAYPAL_ID_RE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{13,20}\b")


def unsafe_reason(text: str) -> str | None:
    """Why a recorded row must not ship, or None when it looks clean."""
    if EMAIL_RE.search(text):
        return "email address"
    if SECRET_RE.search(text):
        return "API key or token"
    if PAYPAL_ID_RE.search(text):
        return "raw PayPal id"
    return None


def collect_rows() -> tuple[list[dict], list[dict]]:
    """(rows to ship, skipped rows with reasons) from the llm_cache table, sorted by key."""
    from sqlmodel import select

    from app.db import session_scope
    from app.models import LlmCache

    keep: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    with session_scope() as db:
        rows = list(db.exec(select(LlmCache).order_by(LlmCache.id.desc())).all())  # newest first per key
    for row in rows:
        if not row.key or row.key in seen:
            continue
        seen.add(row.key)
        try:
            data = json.loads(row.json or "{}")
        except ValueError:
            skipped.append({"key": row.key, "reason": "invalid json"})
            continue
        if not isinstance(data, dict) or "turn" not in data:
            skipped.append({"key": row.key, "reason": "not a recorded turn"})
            continue
        reason = unsafe_reason(row.json)
        if reason:
            skipped.append({"key": row.key, "reason": reason})
            continue
        keep.append(
            {
                "key": row.key,
                "provider": data.get("provider") or "",
                "model": data.get("model") or "",
                "json": row.json,
                # the day it was recorded: a replayed trace row says "recorded 3 Nov"
                "recorded": row.ts.date().isoformat() if row.ts else "",
            }
        )
    keep.sort(key=lambda r: r["key"])
    return keep, skipped


def export(out: Path, *, dry_run: bool = False) -> dict:
    keep, skipped = collect_rows()
    if not dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in keep), encoding="utf-8")
    identities = sorted({f"{r['provider']}/{r['model']}" for r in keep})
    return {
        "written": len(keep),
        "skipped": skipped,
        "identities": identities,
        "out": str(out),
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--out", default=str(SHIPPED_CACHE_PATH), help="output .jsonl (default: app/fixtures/llm_cache.jsonl)"
    )
    p.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = p.parse_args(argv)
    result = export(Path(args.out), dry_run=args.dry_run)
    for s in result["skipped"]:
        print(json.dumps({"skipped": s["key"][:12], "reason": s["reason"]}))
    print(
        json.dumps({k: v for k, v in result.items() if k != "skipped"} | {"skipped": len(result["skipped"])})
    )
    ok = result["written"] > 0
    print("PASS" if ok else "FAIL (no recorded turns to export)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
