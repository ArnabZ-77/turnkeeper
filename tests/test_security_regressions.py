import os

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skipif(not os.path.isdir(os.path.join("app", "static")), reason="SPA not built")
def test_spa_fallback_never_serves_files_outside_static(settings):
    from app.main import app

    with TestClient(app) as c:
        for path in ("/..%2F..%2F.env", "/..%2F..%2Fpyproject.toml", "/%2E%2E/%2E%2E/README.md"):
            r = c.get(path)
            assert r.status_code == 200
            assert "<html" in r.text.lower(), f"{path} leaked a non-SPA file"
            assert "PAYPAL_CLIENT_SECRET" not in r.text
            assert "[project]" not in r.text
