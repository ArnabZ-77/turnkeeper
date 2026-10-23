from fastapi.testclient import TestClient


def test_healthz_boots_without_credentials(settings):
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["env"] == "sandbox"


def test_settings_refuse_live(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("PAYPAL_ENV", "live")
    try:
        Settings()
    except Exception as e:  # pydantic ValidationError
        assert "sandbox" in str(e)
    else:
        raise AssertionError("Settings must refuse PAYPAL_ENV=live")


def test_deployed_app_refuses_default_signing_secrets(monkeypatch):
    """M1 review: an https deployment must not sign mandates/invites with the public default secret."""
    import pytest

    from app.config import Settings, check_deploy_secrets

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://turnkeeper.example")
    monkeypatch.setenv("MANDATE_HMAC_SECRET", "dev-only-change-me")
    monkeypatch.setenv("SESSION_SECRET", "a-long-random-session-secret-value")
    with pytest.raises(RuntimeError, match="MANDATE_HMAC_SECRET"):
        check_deploy_secrets(Settings())
    monkeypatch.setenv("MANDATE_HMAC_SECRET", "short")
    with pytest.raises(RuntimeError):
        check_deploy_secrets(Settings())
    monkeypatch.setenv("MANDATE_HMAC_SECRET", "another-long-random-hmac-secret")
    assert check_deploy_secrets(Settings()) == []


def test_local_app_only_warns_about_default_secrets(monkeypatch, caplog):
    from app.config import Settings, check_deploy_secrets

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("SESSION_SECRET", "dev-only-change-me")
    with caplog.at_level("WARNING", logger="turnkeeper.config"):
        assert "SESSION_SECRET" in check_deploy_secrets(Settings())
    assert "development default" in caplog.text


def test_live_gateway_records_paypal_calls(settings):
    """M1 review: the process gateway persists every call (incl. 429s) to paypal_calls."""
    from app.engine.state import record_paypal_call
    from app.paypal.gateway import get_gateway, set_gateway

    set_gateway(None)
    try:
        assert get_gateway().record is record_paypal_call
    finally:
        set_gateway(None)
