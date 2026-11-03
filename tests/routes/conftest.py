import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from fakes import Collector, FakePayPal, vault_members  # noqa: E402


@pytest.fixture(autouse=True)
def clean_app(monkeypatch):
    """Fresh DB, bus and task registry; no LLM key so the agent runs in rules mode; sandbox credentials."""
    from app.config import get_settings
    from app.db import reset_db
    from app.engine import rounds, tasks
    from app.engine.events import reset_event_bus

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TURNKEEPER_FREEZE", "0")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-TEST")
    monkeypatch.setenv("TK_MAX_ACTIVE_SESSIONS", "6")
    monkeypatch.setenv("TK_SESSION_CREATES_PER_IP_HOUR", "1000")  # most tests open many sessions from one IP
    get_settings.cache_clear()
    reset_db()
    reset_event_bus()
    tasks._TASKS.clear()
    monkeypatch.setattr(rounds, "_TOOLKIT", None)
    from app.routes import join as join_routes

    join_routes.SETUP_LIMITER.reset()
    from app.engine import sessions as _sessions
    from app.routes import webhooks as webhook_routes

    _sessions.session_limiter().reset()
    webhook_routes.WEBHOOK_LIMITER.reset()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app():
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


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


@pytest.fixture
def session(client) -> dict:
    """GET /api/session once: the client now carries the tk_sid cookie; returns the session shape."""
    r = client.get("/api/session")
    assert r.status_code == 200, r.text
    # The body no longer carries the id (it is the HttpOnly cookie); tests read the event bus by it.
    return {**r.json(), "sid": client.cookies.get("tk_sid")}


@pytest.fixture
def vaulted(session) -> dict:
    vault_members(session["circle_id"])
    return session


def wait_for_task(client: TestClient, task_id: str, timeout: float = 15.0) -> dict:
    """Poll GET /api/tasks/{id} until the background run finishes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/tasks/{task_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["done"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish within {timeout}s")
