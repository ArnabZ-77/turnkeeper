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
