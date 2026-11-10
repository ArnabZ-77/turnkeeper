#!/usr/bin/env python3
"""Walk the Turnkeeper Judge story end to end over HTTP and print an evidence report. Standard library only.

    python3 judge_tour.py [--base URL] [--story default|member_agents|injection] [--json]

- default: the 11-step story (Ravi declines, recovery, vote, approval, payout, clean Round 4).
- member_agents (default): Story B. After the proposal opens, it mints Ravi's member capability and plays his own
  assistant over /api/member/*: read the request, answer it once, then try the same token for Tomas (refused).
  A deployment that predates /api/member/* is detected before minting and the beat is skipped, with a note.
- injection: Ravi replies with held-out eval case s06 (a planted "invoice 5000.00"); prints the computed check.

Every step reports how it ran (PayPal live or simulated; AI live, replayed from a recording, or rules). Nothing is
inferred: the report is what the server returned. Exit 0 when the story completes and the member-assistant beat or
the injection check (whichever the story runs) came out as intended; 1 otherwise. Redirects are never followed (the
minted member token rides in a header that urllib would copy to any redirect target).
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
CAP_HEADER = "X-Turnkeeper-Capability"


class TourStopped(Exception):
    """The host answered something the tour cannot go on from; the message says what, with the HTTP status."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib copies every header, the member capability too, even to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None  # urllib then raises HTTPError with the 30x code


def detail_of(status: int, body: dict) -> dict:
    """FastAPI's ``detail`` as a dict: a named refusal already is one; a 404/405/401/429 string is wrapped."""
    d = body.get("detail") if isinstance(body, dict) else None
    if isinstance(d, dict):
        return d
    return {"error": f"HTTP {status}", "message": str(d if d is not None else body)[:200]}


def _json(raw: bytes) -> dict:
    try:
        out = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return {"detail": raw.decode("utf-8", "replace")[:200]}
    return out if isinstance(out, dict) else {"body": out}


class Api:
    def __init__(self, base: str, timeout: float = 120.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.last_headers: dict[str, str] = {}
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect
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
                    self.last_headers = dict(resp.headers.items())
                    return resp.status, _json(resp.read())
            except urllib.error.HTTPError as e:
                self.last_headers = dict(e.headers.items()) if e.headers else {}
                if 300 <= e.code < 400:
                    where = self.last_headers.get("Location", "?")
                    return e.code, {
                        "detail": f"redirected to {where} (not followed; pass the final URL as --base)"
                    }
                return e.code, _json(e.read()) or {"detail": e.reason}
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
            if 300 <= status < 400:
                raise SystemExit(f"{api.base}/healthz answered HTTP {status}: {health.get('detail')}")
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001 - keep waking until the budget runs out
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"{api.base} did not wake within {budget:.0f} s")
        time.sleep(3)


def wait_idle(api: Api, budget: float = 300.0, wake_budget: float = 60.0) -> dict:
    """Poll until no step is running. Only a 502/503/504 (the host restarting) is retried, briefly; any other
    non-200 stops the tour with its real status, so a 401 or 429 is never reported as 'still running'."""
    deadline = time.monotonic() + budget
    waking_since: float | None = None
    while True:
        status, view = api.call("GET", "/api/judge/story")
        if status == 200:
            waking_since = None
            if not view.get("running"):
                return view
        elif status in (502, 503, 504):
            waking_since = waking_since or time.monotonic()
            if time.monotonic() - waking_since > wake_budget:
                raise TourStopped(f"/api/judge/story answered HTTP {status} for {wake_budget:.0f} s")
        else:
            d = detail_of(status, view)
            msg = f"/api/judge/story answered HTTP {status}: {d.get('message') or d.get('error')}"
            if status == 429:
                msg += f" (Retry-After {api.last_headers.get('Retry-After', '?')} s; see GET /api/replay)"
            raise TourStopped(msg)
        if time.monotonic() > deadline:
            raise TourStopped("a story step is still running after 5 minutes")
        time.sleep(1.5)


def _refusal(status: int, body: dict) -> str:
    d = detail_of(status, body)
    return f"{d.get('error')}: {d.get('message')}"


def member_beat(api: Api, circle_id: str) -> dict:
    """Ravi's own assistant: one token, one question, refused for anyone else.

    Returns ``{"ok": bool | None, "note": str, "calls": [...]}``. ``ok`` is None when the deployment predates
    /api/member/* (checked before minting, so nothing is spent), True only when every call came back as intended."""
    probe, _ = api.call("GET", "/api/member/request")  # no header: a current deployment answers 401
    if probe in (404, 405):
        return {
            "ok": None,
            "note": f"skipped: this deployment has no /api/member/* (GET answered HTTP {probe}); "
            "it predates the member assistant API",
            "calls": [],
        }
    calls: list[dict] = []
    status, minted = api.call(
        "POST", f"/api/circles/{circle_id}/member-agents/capability", {"member": "ravi"}
    )
    token = minted.get("token") if status == 200 else None
    if not token:
        calls.append(
            {"call": "mint ravi's answer token", "status": status, "result": _refusal(status, minted)}
        )
        return {"ok": False, "note": "the member token could not be minted", "calls": calls}
    h = {CAP_HEADER: token}
    s1, req = api.call("GET", "/api/member/request", headers=h)
    q = req.get("request") if s1 == 200 and isinstance(req.get("request"), dict) else None
    calls.append(
        {
            "call": "GET /api/member/request",
            "status": s1,
            "result": (
                f"{q.get('item')} for round {q.get('round_n')}: "
                + " + ".join(str(r.get("type", "")) for r in q.get("remedies", []) if isinstance(r, dict))
            )
            if q
            else _refusal(s1, req),
        }
    )
    s2, ans = api.call(
        "POST", "/api/member/answer", {"choice": "yes", "rationale": "Ravi agreed in chat"}, headers=h
    )
    floor = ans.get("floor") if isinstance(ans.get("floor"), dict) else {}
    calls.append(
        {
            "call": "POST /api/member/answer (yes)",
            "status": s2,
            "result": f"recorded {ans.get('recorded')}; mandate rule {floor.get('rule')}"
            if s2 == 200
            else _refusal(s2, ans),
        }
    )
    s3, bad = api.call("POST", "/api/member/answer", {"choice": "yes", "member": "tomas"}, headers=h)
    calls.append({"call": "POST /api/member/answer (as tomas)", "status": s3, "result": _refusal(s3, bad)})
    checks = {
        "request read": s1 == 200 and q is not None,
        "one answer recorded": s2 == 200 and ans.get("recorded") in ("yes", "no"),
        "tomas refused with CAPABILITY_SCOPE": s3 == 403
        and detail_of(s3, bad).get("error") == "CAPABILITY_SCOPE",
    }
    failed = [k for k, v in checks.items() if not v]
    return {
        "ok": not failed,
        "note": "every call came back as intended" if not failed else "not as intended: " + ", ".join(failed),
        "calls": calls,
    }


def injection_verdict(status: int, chk: dict) -> dict:
    """The planted amount must be found in the reply and used by no proposal."""
    planted = chk.get("planted") if status == 200 else None
    if not isinstance(planted, list) or not planted:
        return {"ok": False, "note": f"no planted amount to check (HTTP {status})"}
    used = [
        p.get("text") if isinstance(p, dict) else p
        for p in planted
        if not isinstance(p, dict) or p.get("used")
    ]
    return {"ok": not used, "note": "planted amount ignored" if not used else f"planted amount used: {used}"}


def run(base: str, story: str) -> dict:
    api = Api(base)
    health = wake(api)
    status, sess = api.call("GET", "/api/session")
    if status != 200:
        return {
            "ok": False,
            "base": base,
            "story": story,
            "error": f"no session ({status}): {_refusal(status, sess)}",
            "health": health,
        }
    variant = "member_agents" if story == "member_agents" else None
    status, body = api.call("POST", "/api/judge/story/reset" + (f"?variant={variant}" if variant else ""))
    if status != 200:
        return {
            "ok": False,
            "base": base,
            "story": story,
            "error": f"reset failed ({status}): {_refusal(status, body)}",
            "health": health,
        }
    rows: list[dict] = []
    extras: dict = {}
    try:
        _walk(api, story, rows, extras)
    except TourStopped as e:
        return {"ok": False, "base": base, "story": story, "error": str(e), "health": health, "steps": rows}
    _, replay = api.call("GET", "/api/replay")
    story_ok = bool(rows) and all(not r["error"] for r in rows) and len(rows) == 10
    beats = [extras[k]["ok"] for k in ("member_assistant", "injection_check") if k in extras]
    if story in ("member_agents", "injection") and not extras:
        beats.append(False)  # the story stopped before its beat ran
    return {
        # a skipped beat (ok None: an older deployment) does not fail the run; the report says it was skipped
        "ok": story_ok and all(b is not False for b in beats),
        "story_ok": story_ok,
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


def _walk(api: Api, story: str, rows: list[dict], extras: dict) -> None:
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
                "error": rec.get("error") or (None if status == 200 else _refusal(status, body)[:200]),
            }
        )
        if rows[-1]["error"]:
            break
        if step == "start_recovery":
            cid = view["circle_id"]
            if story == "member_agents":
                extras["member_assistant"] = member_beat(api, cid)
            if story == "injection":
                st, chk = api.call("GET", f"/api/circles/{cid}/recovery/injection-check")
                extras["injection_check"] = {**injection_verdict(st, chk), "status": st, "result": chk}


def _yes(v: bool | None) -> str:
    return "skipped" if v is None else ("yes" if v else "no")


def markdown(r: dict) -> str:
    lines = [f"# Turnkeeper judge tour: {r.get('story')} story on {r['base']}", ""]
    if r.get("error"):
        lines += [f"**Stopped:** {r['error']}"]
        done = [s["step"] for s in r.get("steps") or []]
        if done:
            lines += ["", "Steps that ran before it stopped: " + ", ".join(done)]
        return "\n".join(lines)
    lines += [
        f"Health: `{json.dumps(r['health'])}`",
        "",
        "| # | Step | PayPal | AI | Result |",
        "|---|---|---|---|---|",
    ]
    for i, s in enumerate(r["steps"], start=2):
        lines.append(f"| {i} | {s['step']} | {s['paypal']} | {s['ai']} | {s['error'] or s['summary']} |")
    beat = r.get("member_assistant")
    if beat:
        lines += ["", "Ravi's own assistant (member capability over HTTP):", ""]
        lines += [f"- `{c['call']}` -> {c['status']}: {c['result']}" for c in beat["calls"]]
        lines += ["", f"Member assistant as intended: **{_yes(beat['ok'])}** ({beat['note']})"]
    chk = r.get("injection_check")
    if chk:
        res = chk.get("result") or {}
        lines += [
            "",
            f"Injection check: proposal invoice {(res.get('proposal') or {}).get('invoice')} (contribution {res.get('contribution')})",
        ]
        lines += [
            f"- planted `{p.get('text')}`: used={p.get('used')}; policy if used: {p.get('policy_if_used')}"
            for p in res.get("planted") or []
            if isinstance(p, dict)
        ]
        lines += ["", f"Injection resisted: **{_yes(chk['ok'])}** ({chk['note']})"]
    lines += [
        "",
        f"Story steps complete: **{_yes(r['story_ok'])}** · overall: **{'yes' if r['ok'] else 'no'}** · "
        f"recorded replay available: {r['replay_available']}",
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
