import os

import pytest

# Tests never touch real PayPal or Anthropic. Force a sandbox config with fake credentials.
os.environ.setdefault("PAYPAL_ENV", "sandbox")
os.environ.setdefault("PAYPAL_CLIENT_ID", "test-client-id")
os.environ.setdefault("PAYPAL_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("PAYPAL_WEBHOOK_ID", "WH-TEST")
os.environ.setdefault("MANDATE_HMAC_SECRET", "test-hmac-secret")
os.environ.setdefault("TK_DB_PATH", ":memory:")


@pytest.fixture
def settings():
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()
