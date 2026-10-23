import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

from engine_fakes import FakeEngine  # noqa: E402


@pytest.fixture
def fake_engine(monkeypatch):
    return FakeEngine().install(monkeypatch)


@pytest.fixture
def fake_engine_factory(monkeypatch):
    def _make(**kwargs) -> FakeEngine:
        return FakeEngine(**kwargs).install(monkeypatch)

    return _make


LLM_ENV = ("TK_LLM_PROVIDER", "TK_LLM_API_KEY", "TK_LLM_BASE_URL", "TK_LLM_MODEL", "TK_LLM_MAX_TOKENS",
           "TK_LLM_REASONING_EFFORT", "TK_LIVE_LLM_PER_IP_HOUR")  # fmt: skip


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    """Agent tests never see a developer's .env LLM settings; the replay cache is off unless a test opts in."""
    from app.agent import llm
    from app.config import get_settings

    for name in LLM_ENV:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("TK_LIVE_LLM_PER_IP_HOUR", "10")
    monkeypatch.setenv("TK_LLM_CACHE", "off")
    llm.reset_live_calls()
    get_settings.cache_clear()
    yield
    llm.reset_live_calls()
    get_settings.cache_clear()


@pytest.fixture
def anthropic_key(monkeypatch):
    """Pretend an API key is configured (tests always pass a fake client, so no network)."""
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def no_anthropic_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Collector:
    """An async publish() that records events."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event: dict) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.get("kind") for e in self.events if e.get("type") == "trace"]

    def tools_called(self) -> list[str]:
        return [e["tool"] for e in self.events if e.get("type") == "trace" and e.get("kind") == "tool_call"]


@pytest.fixture
def collector():
    return Collector()
