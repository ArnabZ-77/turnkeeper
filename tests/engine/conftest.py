import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import Collector, FakePayPal, vault_members  # noqa: E402


@pytest.fixture(autouse=True)
def clean_engine(monkeypatch):
    """Fresh in-memory DB, event bus and task registry for every test; no LLM key, sandbox credentials."""
    from app.config import get_settings
    from app.db import reset_db
    from app.engine import rounds, tasks
    from app.engine.events import reset_event_bus

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TURNKEEPER_FREEZE", "0")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-TEST")
    get_settings.cache_clear()
    reset_db()
    reset_event_bus()
    tasks._TASKS.clear()
    monkeypatch.setattr(rounds, "_TOOLKIT", None)
    yield
    get_settings.cache_clear()


@pytest.fixture
def session():
    """A session with its own fixture clone (bypasses the active-session cap)."""
    from app.engine.sessions import create_session

    return create_session(enforce_cap=False)


@pytest.fixture
def circle_id(session) -> str:
    return session.circle_id


@pytest.fixture
def vaulted_circle(circle_id) -> str:
    vault_members(circle_id)
    return circle_id


@pytest.fixture
def paypal(monkeypatch) -> FakePayPal:
    return FakePayPal().install(monkeypatch)


@pytest.fixture
def paypal_factory(monkeypatch):
    def _make(**kwargs) -> FakePayPal:
        return FakePayPal(**kwargs).install(monkeypatch)

    return _make


@pytest.fixture
def collector() -> Collector:
    return Collector()
