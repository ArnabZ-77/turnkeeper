"""POST /webhooks/paypal (always 200 after persisting) and GET /api/debug/webhooks (last 20 rows)."""

import json

from app.engine import webhooks as wh


def _event(i: int, event_type: str = "PAYMENT.CAPTURE.COMPLETED") -> dict:
    return {"id": f"WH-ROUTE-{i:03d}", "event_type": event_type, "resource": {"id": f"CAP-{i:03d}"}}


def test_webhook_route_persists_and_answers_200(client, monkeypatch):
    seen = []

    async def fake_verify(headers, body, webhook_id):
        seen.append(webhook_id)
        return True

    monkeypatch.setattr(wh, "_verify", fake_verify)
    r = client.post("/webhooks/paypal", json=_event(1), headers={"PAYPAL-TRANSMISSION-ID": "t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["verified"] is True and body["duplicate"] is False
    assert body["handled"] is False and body["reason"] == "capture not ours"
    assert seen == ["WH-TEST"]
    dup = client.post("/webhooks/paypal", json=_event(1))
    assert dup.status_code == 200 and dup.json()["duplicate"] is True

    rows = client.get("/api/debug/webhooks").json()
    assert len(rows) == 1
    assert set(rows[0]) == {"id", "event_id", "event_type", "resource_id", "verified", "received_at"}
    assert rows[0]["event_id"] == "WH-ROUTE-001" and rows[0]["verified"] is True
    assert rows[0]["received_at"].endswith("Z")


def test_webhook_route_is_200_for_garbage(client):
    r = client.post(
        "/webhooks/paypal", content=b"\xff\xfe not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False


def test_webhook_route_survives_handler_crash(client, monkeypatch):
    async def boom(headers, body):
        raise RuntimeError("kaboom")

    import app.routes.webhooks as route

    monkeypatch.setattr(route, "handle_event", boom)
    r = client.post("/webhooks/paypal", json=_event(2))
    assert r.status_code == 200 and r.json() == {"ok": False, "error": "RuntimeError"}


def test_debug_webhooks_lists_last_20_newest_first(client, monkeypatch):
    async def fake_verify(headers, body, webhook_id):
        return False

    monkeypatch.setattr(wh, "_verify", fake_verify)
    for i in range(25):
        assert client.post("/webhooks/paypal", content=json.dumps(_event(i)).encode()).status_code == 200
    rows = client.get("/api/debug/webhooks").json()
    assert len(rows) == 20
    assert rows[0]["event_id"] == "WH-ROUTE-024" and rows[-1]["event_id"] == "WH-ROUTE-005"
    assert all(r["verified"] is False for r in rows)
    assert all("payload" not in r for r in rows)  # bodies stay in the DB, never on the debug wire


def test_debug_webhooks_needs_the_demo_token_when_deployed(client, monkeypatch):
    """M1 review: on an https deployment the listing (capture/payout ids) is not public."""
    from app.config import get_settings

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://turnkeeper.example")
    monkeypatch.setenv("MCP_DEMO_TOKEN", "debug-test-token")
    get_settings.cache_clear()
    assert client.get("/api/debug/webhooks").status_code == 401
    assert client.get("/api/debug/webhooks", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/debug/webhooks", headers={"Authorization": "Bearer debug-test-token"})
    assert ok.status_code == 200 and ok.json() == []
    monkeypatch.setenv("MCP_DEMO_TOKEN", "")
    get_settings.cache_clear()
    assert client.get("/api/debug/webhooks", headers={"Authorization": "Bearer "}).status_code == 401
