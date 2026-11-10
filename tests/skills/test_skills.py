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
    calls = {c["call"]: c for c in report["member_assistant"]}
    assert calls["POST /api/member/answer (yes)"]["status"] == 200
    refused = calls["POST /api/member/answer (as tomas)"]
    assert refused["status"] == 403 and "CAPABILITY_SCOPE" in refused["result"]
    md = tour.markdown(report)
    assert "Story complete: **yes**" in md and "CAPABILITY_SCOPE" in md
