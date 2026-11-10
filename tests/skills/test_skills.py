"""Agent Skills shipped in skills/: spec compliance (agentskills.io), the plugin marketplace, and a live judge tour.

The tour runs scripts/judge_tour.py against the real app on a local port (PayPal simulated, AI in rules mode), so the
skill's script, the member capability API and the Story B flow are exercised exactly as a judge's agent would.
"""

from __future__ import annotations

import importlib.util
import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SKILLS = sorted(p for p in (REPO / "skills").iterdir() if (p / "SKILL.md").is_file())
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: no YAML frontmatter"
    head, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


def test_there_are_skills():
    assert {p.name for p in SKILLS} == {"turnkeeper-member-assistant", "turnkeeper-judge-tour"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_skill_follows_the_agent_skills_spec(skill: Path):
    meta, body = _frontmatter(skill / "SKILL.md")
    assert set(meta) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    name = meta["name"]
    assert 1 <= len(name) <= 64 and NAME_RE.match(name) and name == skill.name
    assert 1 <= len(meta["description"]) <= 1024
    assert "Use when" in meta["description"]  # says when to use it, not only what it does
    if "compatibility" in meta:
        assert 1 <= len(meta["compatibility"]) <= 500
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in (meta.get("metadata") or {}).items())
    assert len(body.splitlines()) < 500
    for ref in re.findall(
        r"\]\(((?:references|scripts|assets)/[^)]+)\)", body
    ):  # one level deep, and present
        assert (skill / ref).is_file(), f"{skill.name}: missing {ref}"
    for script in (skill / "scripts").glob("*.py"):
        compile(script.read_text(encoding="utf-8"), str(script), "exec")
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert not re.search(r"tkc1\.[A-Za-z0-9_-]{20,}", text)  # no real token ever ships


def test_marketplace_lists_exactly_the_skills():
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    listed = {Path(p).name for plugin in market["plugins"] for p in plugin["skills"]}
    assert listed == {p.name for p in SKILLS}
    for plugin in market["plugins"]:
        for p in plugin["skills"]:
            assert (REPO / p / "SKILL.md").is_file()


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_member_script_takes_a_token_or_a_passbook_link():
    member = _load(REPO / "skills/turnkeeper-member-assistant/scripts/member.py")
    assert member.token_from("tkc1.a.b") == "tkc1.a.b"
    assert member.token_from("https://host/m#tkc1.a.b") == "tkc1.a.b"
    with pytest.raises(SystemExit):
        member.token_from("not-a-token")


def test_member_script_sends_a_links_token_only_to_that_link_s_host(monkeypatch):
    member = _load(REPO / "skills/turnkeeper-member-assistant/scripts/member.py")
    monkeypatch.delenv("TURNKEEPER_BASE", raising=False)
    assert member.resolve_base("https://Other.Example/m#tkc1.a.b", None) == "https://other.example"
    assert member.resolve_base("http://localhost:8000/m#tkc1.a.b", None) == "http://localhost:8000"
    assert (
        member.resolve_base("https://other.example/m#tkc1.a.b", "https://other.example/")
        == "https://other.example"
    )
    with pytest.raises(SystemExit, match="another host"):  # a conflicting --base is refused, not used
        member.resolve_base("http://localhost:8000/m#tkc1.a.b", member.DEFAULT_BASE)
    monkeypatch.setenv("TURNKEEPER_BASE", "https://env.example")
    assert (
        member.resolve_base("https://other.example/m#tkc1.a.b", None) == "https://other.example"
    )  # link wins
    assert (
        member.resolve_base("tkc1.a.b", None) == "https://env.example"
    )  # a bare token uses the configured host
    assert member.resolve_base("tkc1.a.b", "http://127.0.0.1:9000") == "http://127.0.0.1:9000"


@pytest.mark.parametrize(
    "base",
    ["http://turnkeeper-s8xv.onrender.com", "http://10.0.0.5:8000", "ftp://host", "file:///etc", "host"],
)
def test_member_script_needs_https_except_on_localhost(base):
    member = _load(REPO / "skills/turnkeeper-member-assistant/scripts/member.py")
    with pytest.raises(SystemExit):
        member.check_base(base)
    with pytest.raises(SystemExit):
        member.resolve_base(f"{base}/m#tkc1.a.b", None)
    with pytest.raises(SystemExit):  # call() checks too, so no caller can skip the rule
        member.call(base, "GET", "/api/member/request", "tkc1.a.b")


@pytest.fixture
def redirecting_server():
    """A local host that answers every request with a 302 to /stolen and records what reached it."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[tuple[str, str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def _any(self) -> None:
            seen.append((self.command, self.path, self.headers.get("X-Turnkeeper-Capability")))
            self.send_response(302)
            self.send_header("Location", "/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = _any

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()
    server.server_close()


def test_member_script_never_follows_a_redirect_with_the_token(redirecting_server):
    base, seen = redirecting_server
    member = _load(REPO / "skills/turnkeeper-member-assistant/scripts/member.py")
    status, out = member.call(base, "POST", "/api/member/answer", "tkc1.a.b", {"choice": "yes"})
    assert status == 302 and "refused" in out["detail"]
    assert [p for _, p, _ in seen] == ["/api/member/answer"]  # /stolen never saw the token


def test_judge_tour_never_follows_a_redirect_with_the_minted_token(redirecting_server):
    base, seen = redirecting_server
    tour = _load(REPO / "skills/turnkeeper-judge-tour/scripts/judge_tour.py")
    status, out = tour.Api(base).call("GET", "/api/member/request", headers={tour.CAP_HEADER: "tkc1.a.b"})
    assert status == 302 and "not followed" in out["detail"]
    assert [p for _, p, _ in seen] == ["/api/member/request"]


# --- judge tour against a scripted host: string details, an older deployment, the computed verdicts --------------

STEPS = (
    [f"step{i}" for i in range(4)] + ["start_recovery"] + [f"later{i}" for i in range(4)] + ["clean_round4"]
)


class FakeHost:
    """Plays the Judge story endpoints; ``member`` maps (method, path, as_tomas) to (status, body) overrides."""

    def __init__(self, member: dict | None = None, story: dict | None = None, injection: dict | None = None):
        self.i = 0
        self.calls: list[tuple[str, str]] = []
        self.member = {
            ("GET", "/api/member/request", False): (401, {"detail": "missing X-Turnkeeper-Capability"}),
            ("POST", "/api/circles/c1/member-agents/capability", False): (200, {"token": "tkc1.a.b"}),
            ("GET", "/api/member/request", True): (
                200,
                {"request": {"item": "consent", "round_n": 3, "remedies": [{"type": "repay_by_invoice"}]}},
            ),
            ("POST", "/api/member/answer", False): (200, {"recorded": "yes", "floor": {"rule": "r1"}}),
            ("POST", "/api/member/answer", True): (
                403,
                {"detail": {"error": "CAPABILITY_SCOPE", "message": "for ravi only"}},
            ),
            **(member or {}),
        }
        self.story = story or {}
        self.injection = injection or (200, {"planted": [{"text": "5000.00", "used": False}]})
        self.last_headers: dict[str, str] = {}

    def call(self, method, path, body=None, headers=None):
        self.calls.append((method, path))
        if path == "/api/judge/story" and path in self.story:
            return self.story[path]
        if path == "/healthz":
            return 200, {"env": "test"}
        if path == "/api/session" or path.startswith("/api/judge/story/reset") or path == "/api/replay":
            return 200, {"ok": True}
        if path == "/api/judge/story":
            done = [
                {"step": n, "status": "done", "paypal": "simulated", "ai": "rules"} for n in STEPS[: self.i]
            ]
            return 200, {
                "running": False,
                "complete": self.i >= len(STEPS),
                "next": {"step": STEPS[min(self.i, len(STEPS) - 1)]},
                "steps": done,
                "circle_id": "c1",
            }
        if path.startswith("/api/judge/story/next"):
            self.i += 1
            return 200, {"ok": True}
        if path.endswith("/injection-check"):
            return self.injection
        tomas = bool(body and body.get("member") == "tomas")
        key = (method, path, bool(headers) if method == "GET" else tomas)
        return self.member[key]


def _tour_with(monkeypatch, host: FakeHost):
    tour = _load(REPO / "skills/turnkeeper-judge-tour/scripts/judge_tour.py")
    monkeypatch.setattr(tour, "Api", lambda base: host)
    return tour


def test_judge_tour_passes_when_the_member_beat_works(monkeypatch):
    tour = _tour_with(monkeypatch, FakeHost())
    report = tour.run("https://host", "member_agents")
    assert report["ok"] and report["story_ok"] and report["member_assistant"]["ok"] is True
    assert "Member assistant as intended: **yes**" in tour.markdown(report)


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (404, "Not Found"),
        (405, "Method Not Allowed"),
        (401, "missing X-Turnkeeper-Capability"),
        (429, "rate limited"),
    ],
)
def test_judge_tour_survives_string_details_on_member_calls(monkeypatch, status, detail):
    plain = (status, {"detail": detail})
    host = FakeHost(
        member={
            ("GET", "/api/member/request", True): plain,
            ("POST", "/api/member/answer", False): plain,
            ("POST", "/api/member/answer", True): plain,
        }
    )
    tour = _tour_with(monkeypatch, host)
    report = tour.run("https://host", "member_agents")  # no AttributeError on a str detail
    beat = report["member_assistant"]
    assert beat["ok"] is False and report["story_ok"] and not report["ok"]
    assert all(c["result"] == f"HTTP {status}: {detail}" for c in beat["calls"][1:] if c["status"] == status)
    md = tour.markdown(report)
    assert "Member assistant as intended: **no**" in md and "overall: **no**" in md


def test_judge_tour_skips_the_beat_on_a_deployment_without_the_member_api(monkeypatch):
    host = FakeHost(member={("GET", "/api/member/request", False): (404, {"detail": "Not Found"})})
    tour = _tour_with(monkeypatch, host)
    report = tour.run("https://host", "member_agents")
    assert (
        report["member_assistant"]["ok"] is None and "no /api/member/*" in report["member_assistant"]["note"]
    )
    assert not any("capability" in p for _, p in host.calls)  # nothing minted, nothing spent
    assert report["ok"]  # the story itself completed; the report says the beat was skipped
    assert "Member assistant as intended: **skipped**" in tour.markdown(report)


@pytest.mark.parametrize(
    "override",
    [
        {("POST", "/api/member/answer", True): (200, {"recorded": "yes"})},  # tomas was not refused
        {("POST", "/api/member/answer", True): (404, {"detail": "Not Found"})},
        {
            ("POST", "/api/member/answer", False): (
                403,
                {"detail": {"error": "SET_SUPERSEDED", "message": "x"}},
            )
        },
        {
            ("POST", "/api/circles/c1/member-agents/capability", False): (
                409,
                {"detail": {"error": "CAPABILITY_USED"}},
            )
        },
    ],
)
def test_judge_tour_ok_folds_in_the_member_beat(monkeypatch, override):
    tour = _tour_with(monkeypatch, FakeHost(member=override))
    report = tour.run("https://host", "member_agents")
    assert report["story_ok"] and report["member_assistant"]["ok"] is False and report["ok"] is False
    monkeypatch.setattr(tour, "run", lambda base, story: report)
    assert tour.main(["--story", "member_agents"]) == 1


@pytest.mark.parametrize(
    ("injection", "ok"),
    [
        ((200, {"planted": [{"text": "5000.00", "used": False}]}), True),
        ((200, {"planted": [{"text": "5000.00", "used": True}]}), False),
        ((200, {"planted": []}), False),
        ((404, {"detail": "Not Found"}), False),
    ],
)
def test_judge_tour_ok_folds_in_the_injection_check(monkeypatch, injection, ok):
    tour = _tour_with(monkeypatch, FakeHost(injection=injection))
    report = tour.run("https://host", "injection")
    assert report["story_ok"] and report["injection_check"]["ok"] is ok and report["ok"] is ok
    assert f"Injection resisted: **{'yes' if ok else 'no'}**" in tour.markdown(report)


@pytest.mark.parametrize(
    ("status", "headers", "expect"),
    [
        (401, {}, "HTTP 401: no session"),
        (429, {"Retry-After": "42"}, "Retry-After 42 s"),
        (404, {}, "HTTP 404"),
    ],
)
def test_wait_idle_reports_a_persistent_non_200_truthfully(monkeypatch, status, headers, expect):
    host = FakeHost(story={"/api/judge/story": (status, {"detail": "no session"})})
    host.last_headers = headers
    tour = _tour_with(monkeypatch, host)
    started = time.monotonic()
    report = tour.run("https://host", "default")
    assert time.monotonic() - started < 5  # no five-minute wait
    assert not report["ok"] and expect in report["error"] and "still running" not in report["error"]
    assert "**Stopped:**" in tour.markdown(report)


def test_wait_idle_retries_a_waking_host_briefly(monkeypatch):
    tour = _load(REPO / "skills/turnkeeper-judge-tour/scripts/judge_tour.py")
    answers = [(503, {"detail": "waking"}), (502, {}), (200, {"running": False, "complete": True})]

    class Waking:
        last_headers: dict = {}

        def call(self, method, path, body=None, headers=None):
            return answers.pop(0)

    monkeypatch.setattr(tour.time, "sleep", lambda s: None)
    assert tour.wait_idle(Waking())["complete"] is True
    answers[:] = [(503, {})] * 3
    with pytest.raises(tour.TourStopped, match="HTTP 503"):
        tour.wait_idle(Waking(), wake_budget=-1)


@pytest.fixture
def live_server(monkeypatch):
    import uvicorn

    from app.main import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_judge_tour_runs_story_b_with_a_members_own_assistant(live_server, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TK_LLM_PROVIDER", "none")
    get_settings.cache_clear()
    tour = _load(REPO / "skills/turnkeeper-judge-tour/scripts/judge_tour.py")
    report = tour.run(live_server, "member_agents")
    assert report["ok"], report
    assert [s["step"] for s in report["steps"]][-1] == "clean_round4"
    assert report["member_assistant"]["ok"] is True, report["member_assistant"]
    calls = {c["call"]: c for c in report["member_assistant"]["calls"]}
    assert calls["POST /api/member/answer (yes)"]["status"] == 200
    refused = calls["POST /api/member/answer (as tomas)"]
    assert refused["status"] == 403 and "CAPABILITY_SCOPE" in refused["result"]
    md = tour.markdown(report)
    assert "Story steps complete: **yes**" in md and "overall: **yes**" in md and "CAPABILITY_SCOPE" in md
    assert "Member assistant as intended: **yes**" in md
