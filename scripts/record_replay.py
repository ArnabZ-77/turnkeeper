"""Record a SIMULATED canonical Judge Mode story offline and write it to app/fixtures/replay_canonical.json.

GET /api/replay serves the newest recording made on the deployment (a live-sandbox one first) and falls back to
this shipped file, so a fresh deployment never answers 404. The shipped run is honest about what it is: every
step is labelled ``simulated`` / ``rules`` and ``paypal_mode`` is ``simulated``. No PayPal or model call can
happen: credentials and model keys are blanked before the app is imported, the database is in memory, and the
script refuses to run if anything still looks configured. A live-sandbox recording made on the deployment (run
the story once there) always takes precedence over this file.

Usage (from any directory; run it outside the repo root so no local .env is picked up):
    D:/Desktop/PayPal/turnkeeper/.venv/Scripts/python D:/Desktop/PayPal/turnkeeper/scripts/record_replay.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "fixtures" / "replay_canonical.json"

_OFFLINE_ENV = {
    "PAYPAL_ENV": "sandbox",
    "PAYPAL_CLIENT_ID": "",
    "PAYPAL_CLIENT_SECRET": "",
    "PAYPAL_WEBHOOK_ID": "",
    "TK_SEED_VAULT_IDS": "",
    "TK_DB_PATH": ":memory:",
    "TK_LLM_PROVIDER": "none",
    "TK_LLM_API_KEY": "",
    "TK_LLM_MODEL": "",
    "TK_LLM_BASE_URL": "",
    "TK_LLM_CACHE": "off",
    "ANTHROPIC_API_KEY": "",
    "PUBLIC_BASE_URL": "http://localhost:8000",
    "TURNKEEPER_FREEZE": "0",
}


def record() -> dict:
    os.environ.update(_OFFLINE_ENV)
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if settings.paypal_configured or settings.llm_configured:
        raise SystemExit("refusing to record: PayPal or a model still looks configured (offline only)")

    from app.engine import story
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/session").status_code == 200
        for _ in range(len(story.STEP_NAMES)):
            r = client.post("/api/judge/story/next")
            if r.status_code != 200:
                raise SystemExit(f"story step failed: {r.status_code} {r.text[:300]}")
            body = r.json()
            if body.get("task_id"):
                _wait(client, body["step"])
        data = story.latest_recording()
    if data is None or data.get("paypal_mode") != "simulated":
        raise SystemExit("no simulated recording was produced")
    for key in ("recorded_at", "source"):
        data.pop(key, None)
    data["shipped_note"] = (
        "Recorded offline with scripts/record_replay.py: simulated PayPal (no call made), rules-mode AI. "
        "A recording made on the deployment replaces it."
    )
    return data


def _wait(client, step: str, timeout: float = 30.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get("/api/judge/story").json()
        if {s["step"]: s["status"] for s in view["steps"]}.get(step) == "done":
            return
        if view["running"] is None and view["next"] and view["next"]["step"] == step:
            raise SystemExit(f"story step {step} did not succeed: {view}")
        time.sleep(0.05)
    raise SystemExit(f"story step {step} timed out")


def main() -> int:
    data = record()
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(data['steps'])} steps, paypal_mode={data['paypal_mode']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
