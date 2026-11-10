import os

import pytest

# Tests never touch real PayPal or Anthropic. Force a sandbox config with fake credentials.
os.environ.setdefault("PAYPAL_ENV", "sandbox")
os.environ.setdefault("PAYPAL_CLIENT_ID", "test-client-id")
os.environ.setdefault("PAYPAL_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("PAYPAL_WEBHOOK_ID", "WH-TEST")
os.environ.setdefault("MANDATE_HMAC_SECRET", "test-hmac-secret")
os.environ.setdefault("TK_DB_PATH", ":memory:")
# Never reach a live LLM from tests, whatever keys a developer's .env holds (env vars beat .env). Agent tests
# opt back in per test with fake providers (tests/agent/conftest.py).
os.environ["TK_LLM_PROVIDER"] = "none"
os.environ["TK_LLM_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["TK_LLM_CACHE"] = "off"
os.environ["TK_LLM_MODEL"] = ""
os.environ["TK_LLM_BASE_URL"] = ""
# Real sandbox vault ids in a developer's .env must not leak into the fixture clones tests build.
os.environ["TK_SEED_VAULT_IDS"] = ""
# Local (http) base URL so a deployed .env never trips the https-only startup secrets check in tests.
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:8000")
# Most tests open many sessions from the one TestClient address; the per-IP session budget has its own tests.
os.environ["TK_SESSION_CREATES_PER_IP_HOUR"] = "1000"
os.environ["TK_CSP_MODE"] = "enforce"


@pytest.fixture(autouse=True)
def _fresh_rate_limiters():
    """In-memory per-IP budgets (sessions, webhooks) must not leak between tests."""
    from app.engine import sessions
    from app.routes import member_api, webhooks

    sessions.session_limiter().reset()
    webhooks.WEBHOOK_LIMITER.reset()
    member_api.MEMBER_LIMITER.reset()
    yield


@pytest.fixture(autouse=True)
def _no_shipped_recordings(monkeypatch, tmp_path):
    """app/fixtures/llm_cache.jsonl (the recorded Gemini takes) must not decide replay identity or answers in
    unit tests; the tests that ship recordings point SHIPPED_CACHE_PATH at their own file."""
    from app.agent import llm

    monkeypatch.setattr(llm, "SHIPPED_CACHE_PATH", tmp_path / "no-shipped-cache.jsonl")


@pytest.fixture(autouse=True)
def _no_payout_watchers(monkeypatch):
    """The bounded payout status poll (rounds.watch_payout) sleeps for real; tests that exercise it switch it on
    with short intervals. Everywhere else a PENDING payout stays as the payout call left it."""
    from app.engine import rounds

    monkeypatch.setattr(rounds, "PAYOUT_WATCH_ENABLED", False)


@pytest.fixture
def settings():
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()
