import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_fakes import FakeEngine  # noqa: E402


@pytest.fixture
def fake_engine(monkeypatch):
    """A FakeEngine installed as app.engine.rounds / app.engine.state (+ PolicyDenied / PayPalError)."""
    return FakeEngine().install(monkeypatch)


@pytest.fixture
def fake_engine_factory(monkeypatch):
    def _make(**kwargs) -> FakeEngine:
        return FakeEngine(**kwargs).install(monkeypatch)

    return _make


@pytest.fixture
def demo_token(monkeypatch):
    """Configure the MCP demo token for the duration of a test (settings cache cleared both ways)."""
    from app.config import get_settings

    monkeypatch.setenv("MCP_DEMO_TOKEN", "test-demo-token")
    get_settings.cache_clear()
    yield "test-demo-token"
    get_settings.cache_clear()
