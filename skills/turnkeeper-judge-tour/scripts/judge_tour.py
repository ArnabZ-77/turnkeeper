#!/usr/bin/env python3
"""Walk the Turnkeeper Judge story end to end over HTTP and print an evidence report. Standard library only.

    python3 judge_tour.py [--base URL] [--story default|member_agents|injection] [--json]

- default: the 11-step story (Ravi declines, recovery, vote, approval, payout, clean Round 4).
- member_agents (default): Story B. After the proposal opens, it mints Ravi's member capability and plays his own
  assistant over /api/member/*: read the request, answer it once, then try the same token for Tomas (refused).
- injection: Ravi replies with held-out eval case s06 (a planted "invoice 5000.00"); prints the computed check.

Every step reports how it ran (PayPal live or simulated; AI live, replayed from a recording, or rules). Nothing is
inferred: the report is what the server returned. Exit 0 when the story completes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://turnkeeper-s8xv.onrender.com"


class Api:
    def __init__(self, base: str, timeout: float = 120.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(
        self, method: str, path: str, body: dict | None = None, headers: dict | None = None
    ) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        for attempt in range(3):
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read().decode("utf-8") or "{}")
                except ValueError:
                    return e.code, {"detail": e.reason}
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2)
        raise RuntimeError("unreachable")


def wake(api: Api, budget: float = 150.0) -> dict:
    """The free host sleeps; the first request can take about a minute."""
    deadline = time.monotonic() + budget
    while True:
        try:
            status, health = api.call("GET", "/healthz")
            if status == 200:
                return health
        except Exception:  # noqa: BLE001 - keep waking until the budget runs out
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"{api.base} did not wake within {budget:.0f} s")
        time.sleep(3)


def wait_idle(api: Api, budget: float = 300.0) -> dict:
    deadline = time.monotonic() + budget
    while True:
        status, view = api.call("GET", "/api/judge/story")
        if status == 200 and not view.get("running"):
            return view
        if time.monotonic() > deadline:
            raise SystemExit("a story step is still running after 5 minutes")
        time.sleep(1.5)


def member_beat(api: Api, circle_id: str) -> list[dict]:
    """Ravi's own assistant: one token, one question, refused for anyone else."""
    out: list[dict] = []
    status, minted = api.call(
        "POST", f"/api/circles/{circle_id}/member-agents/capability", {"member": "ravi"}
    )
    if status != 200:
        return [{"call": "mint ravi's answer token", "status": status, "result": minted.get("detail")}]
    h = {"X-Turnkeeper-Capability": minted["token"]}
    s1, req = api.call("GET", "/api/member/request", headers=h)
    q = req.get("request") or {}
    out.append(
        {
            "call": "GET /api/member/request",
            "status": s1,
            "result": f"{q.get('item')} for round {q.get('round_n')}: "
            + " + ".join(r.get("type", "") for r in q.get("remedies", [])),
        }
    )
    s2, ans = api.call(
        "POST", "/api/member/answer", {"choice": "yes", "rationale": "Ravi agreed in chat"}, headers=h
    )
    out.append(
        {
            "call": "POST /api/member/answer (yes)",
            "status": s2,
            "result": f"recorded {ans.get('recorded')}; mandate rule {((ans.get('floor') or {}).get('rule'))}",
        }
    )
    s3, bad = api.call("POST", "/api/member/answer", {"choice": "yes", "member": "tomas"}, headers=h)
    d = bad.get("detail") or {}
    out.append(
        {
            "call": "POST /api/member/answer (as tomas)",
            "status": s3,
            "result": f"{d.get('error')}: {d.get('message')}",
        }
    )
    return out


def run(base: str, story: str) -> dict:
    api = Api(base)
    health = wake(api)
    status, sess = api.call("GET", "/api/session")
    if status != 200:
        return {
            "ok": False,
            "base": base,
            "error": f"no session ({status}): {sess.get('detail') or sess}",
            "health": health,
        }
    variant = "member_agents" if story == "member_agents" else None
    status, _ = api.call("POST", "/api/judge/story/reset" + (f"?variant={variant}" if variant else ""))
    if status != 200:
        return {"ok": False, "base": base, "error": f"reset failed ({status})", "health": health}
    rows: list[dict] = []
    extras: dict = {}
    while True:
        view = wait_idle(api)
        if view.get("complete"):
            break
        step = view["next"]["step"]
        path = "/api/judge/story/next" + (
            "?reply=s06" if story == "injection" and step == "reply_as_ravi" else ""
        )
        status, body = api.call("POST", path)
        view = wait_idle(api)
        rec = next((s for s in view["steps"] if s["step"] == step), {})
        rows.append(
            {
                "step": step,
                "status": rec.get("status"),
                "paypal": rec.get("paypal") or "-",
                "ai": rec.get("ai") or "-",
                "summary": (rec.get("summary") or "")[:140],
                "error": rec.get("error") or (None if status == 200 else str(body.get("detail"))[:200]),
            }
        )
        if rows[-1]["error"]:
            break
        if step == "start_recovery":
            cid = view["circle_id"]
            if story == "member_agents":
                extras["member_assistant"] = member_beat(api, cid)
            if story == "injection":
                _, chk = api.call("GET", f"/api/circles/{cid}/recovery/injection-check")
                extras["injection_check"] = chk
    _, replay = api.call("GET", "/api/replay")
    return {
        "ok": bool(rows) and all(not r["error"] for r in rows) and len(rows) == 10,
        "base": base,
        "story": story,
        "health": {
            k: health.get(k)
            for k in (
                "env",
                "paypal_configured",
                "llm_provider",
                "llm_configured",
                "llm_cache",
                "vault_seeded",
            )
        },
        "steps": rows,
        **extras,
        "replay_available": bool(replay.get("steps") or replay.get("events") or replay.get("ok")),
    }


def markdown(r: dict) -> str:
    lines = [f"# Turnkeeper judge tour: {r.get('story')} story on {r['base']}", ""]
    if r.get("error"):
        return "\n".join(lines + [f"**Stopped:** {r['error']}"])
    lines += [
        f"Health: `{json.dumps(r['health'])}`",
        "",
        "| # | Step | PayPal | AI | Result |",
        "|---|---|---|---|---|",
    ]
    for i, s in enumerate(r["steps"], start=2):
        lines.append(f"| {i} | {s['step']} | {s['paypal']} | {s['ai']} | {s['error'] or s['summary']} |")
    if r.get("member_assistant"):
        lines += ["", "Ravi's own assistant (member capability over HTTP):", ""]
        lines += [f"- `{c['call']}` -> {c['status']}: {c['result']}" for c in r["member_assistant"]]
    if r.get("injection_check"):
        chk = r["injection_check"]
        lines += [
            "",
            f"Injection check: proposal invoice {chk.get('proposal', {}).get('invoice')} (contribution {chk.get('contribution')})",
        ]
        lines += [
            f"- planted `{p['text']}`: used={p['used']}; policy if used: {p['policy_if_used']}"
            for p in chk.get("planted", [])
        ]
    lines += [
        "",
        f"Story complete: **{'yes' if r['ok'] else 'no'}** · recorded replay available: {r['replay_available']}",
    ]
    lines += [
        "",
        "Labels: PayPal `live` = a real sandbox call; `simulated` = no PayPal call. AI `live` = a model call now; `replay` = a recorded model turn; `rules` = deterministic fallback.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--story", choices=["default", "member_agents", "injection"], default="member_agents")
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    a = ap.parse_args(argv)
    r = run(a.base, a.story)
    print(json.dumps(r, indent=1, ensure_ascii=False) if a.json else markdown(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
