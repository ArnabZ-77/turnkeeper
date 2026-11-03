"""Record a LIVE Story B (member agents) run against the real PayPal sandbox and write it to
app/fixtures/replay_member_agents.json.

This is the Nov-1 item in docs/innovation-plan.md: run Story B once, live, end to end, and ship the recording
as a fallback for the video / Advanced Judge if a live run misbehaves on recording day. It is never served as
the canonical /api/replay recording (Story B is always tainted); it is a separate file for a separate UI path.

Unlike scripts/record_replay.py, this REQUIRES real sandbox credentials and seeded vault ids (TK_SEED_VAULT_IDS
in .env) -- it drives the same in-process app used by the live demo, so round 3 captures, the recovery, and the
payout all hit the real PayPal sandbox. The AI side still runs in rules mode unless an LLM key is configured.

Usage (from the repo root, with a filled .env):
    .venv/Scripts/python scripts/record_replay_member_agents.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "fixtures" / "replay_member_agents.json"


def record() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if not settings.paypal_configured:
        raise SystemExit("refusing to record: PayPal sandbox credentials are not configured in .env")
    if settings.member_agents_killed:
        raise SystemExit("refusing to record: TK_MEMBER_AGENTS=0 (member agents are killed)")

    from app.engine import state, story
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/session").status_code == 200
        r = client.post("/api/judge/story/reset", params={"variant": story.VARIANT_B})
        if r.status_code != 200:
            raise SystemExit(f"reset failed: {r.status_code} {r.text[:300]}")
        cid = r.json()["session"]["circle_id"]
        print(f"circle {cid}: Story B reset, live sandbox")

        for name in story.STEP_NAMES[1:]:  # reset already ran
            if name == "votes":
                # Pre-answer any member whose agent would ASK (e.g. a swap moving them to a later round), so
                # the story's own "votes" step (which does not take judge answers) finds nothing pending.
                members = [m.handle for m in state.charter_of(state.load_circle(cid)).members]
                client.post(
                    f"/api/circles/{cid}/member-agents/run", json={"answers": {h: "yes" for h in members}}
                )
            r = client.post(f"/api/judge/story/{name}")
            if r.status_code != 200:
                raise SystemExit(f"story step {name} failed: {r.status_code} {r.text[:500]}")
            body = r.json()
            print(f"  {name}: {body.get('summary')}")
            if body.get("task_id"):
                _wait(client, name)
        data = _latest_recording_for(cid)
    if data is None:
        raise SystemExit("no recording was produced")
    if data.get("paypal_mode") != "live":
        raise SystemExit(
            f"recording is not live (paypal_mode={data.get('paypal_mode')}); refusing to ship it"
        )
    for key in ("recorded_at", "source"):
        data.pop(key, None)
    data["shipped_note"] = (
        "Recorded live against the real PayPal sandbox with scripts/record_replay_member_agents.py: Story B "
        "(member agents), rules-mode AI unless an LLM key was configured at record time. Fallback only; the "
        "demo prefers a fresh live run."
    )
    return data


def _latest_recording_for(circle_id: str) -> dict[str, Any] | None:
    """The newest recording for this circle, canonical or not (Story B is always tainted/non-canonical, so
    ``story.latest_recording()`` -- which only looks at canonical rows -- would miss it)."""
    import json as _json

    from sqlmodel import select

    from app.db import session_scope
    from app.models import ReplayRecording

    with session_scope() as db:
        rec = db.exec(
            select(ReplayRecording)
            .where(ReplayRecording.circle_id == circle_id)
            .order_by(ReplayRecording.created_at.desc())
        ).first()
        if rec is None:
            return None
        return _json.loads(rec.json)


def _wait(client, step: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get("/api/judge/story").json()
        if {s["step"]: s["status"] for s in view["steps"]}.get(step) == "done":
            return
        if view["running"] is None and view["next"] and view["next"]["step"] == step:
            raise SystemExit(f"story step {step} did not succeed: {view}")
        time.sleep(0.5)
    raise SystemExit(f"story step {step} timed out")


def main() -> int:
    data = record()
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(data['steps'])} steps, paypal_mode={data['paypal_mode']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
