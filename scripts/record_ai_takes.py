"""Drive a running local server through every AI moment so its recorded model turns can be shipped.

Start the server with a free key and recording on, against a fresh database, for example:

    TK_LLM_MODEL=gemini-3.5-flash-lite TK_LLM_CACHE=record TK_DB_PATH=./data/takes.db TK_LIVE_LLM_PER_IP_HOUR=500 \
        .venv/Scripts/python -m uvicorn app.main:app --port 8010

then run ``.venv/Scripts/python -I scripts/record_ai_takes.py --base http://127.0.0.1:8010`` and export with
``scripts/export_llm_cache.py``. The takes: (a) Hindi charter compile, (b)+(c) the default Judge story (forecast
nudge, Ravi's reply, the treasurer's tool calls), (d) Story B (member agents explain their decisions), and with
``--reply s06 --skip compile,story_b`` (e) the eval s06 injection reply.
Each step's AI label is printed; a line per take goes to stdout as JSON for docs/live-run/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def wait_idle(c: httpx.Client, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            view = c.get("/api/judge/story").json()
        except httpx.TransportError:  # a kept-alive connection closed mid-poll: poll again
            time.sleep(1.5)
            continue
        if not view.get("running"):
            return view
        if time.monotonic() > deadline:
            raise TimeoutError("story step still running")
        time.sleep(1.5)


def run_story(c: httpx.Client, variant: str | None, timeout: float, reply: str | None = None) -> list[dict]:
    params = {"variant": variant} if variant else None
    r = c.post("/api/judge/story/reset", params=params)
    r.raise_for_status()
    out = []
    while True:
        view = wait_idle(c, timeout)
        if view.get("complete"):
            break
        nxt = view["next"]["step"]
        t0 = time.monotonic()
        params = {"reply": reply} if reply and nxt == "reply_as_ravi" else None
        r = c.post("/api/judge/story/next", params=params)
        if r.status_code >= 400:
            row = {"variant": variant or "default", "step": nxt, "error": r.text[:300]}
            out.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            break
        view = wait_idle(c, timeout)
        rec = next((s for s in view["steps"] if s["step"] == nxt), {})
        row = {
            "variant": (variant or "default") + (f"+{reply}" if reply else ""),
            "step": nxt,
            "secs": round(time.monotonic() - t0, 1),
            "ai": rec.get("ai"),
            "mode": rec.get("mode"),
            "paypal": rec.get("paypal"),
            "error": rec.get("error") or (view.get("last_error") or {}).get("label"),
        }
        out.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if row["error"]:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--skip", default="", help="comma list of takes to skip: compile,default,story_b")
    ap.add_argument(
        "--reply", default=None, help="judge reply for the default story, e.g. s06 (eval injection)"
    )
    a = ap.parse_args()
    skip = set(filter(None, a.skip.split(",")))
    with httpx.Client(base_url=a.base, timeout=a.timeout) as c:
        c.get("/api/session").raise_for_status()
        if "compile" not in skip:
            text = c.get("/api/circles/compile/demo", params={"lang": "hi"}).json()["text"]
            res = c.post("/api/circles/compile", json={"text": text, "lang": "hi"}).json()
            print(
                json.dumps(
                    {
                        "take": "compile_hi",
                        "mode": res.get("mode"),
                        "label": res.get("mode_label"),
                        "ok": res.get("ok"),
                        "sha": (res.get("charter_sha256") or "")[:12],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        rows: list[dict] = []
        if "default" not in skip:
            rows += run_story(c, None, a.timeout, a.reply)
        if "story_b" not in skip:
            rows += run_story(c, "member_agents", a.timeout)
    return 1 if any(r.get("error") for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
