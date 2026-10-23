"""Webhook reconciler: verification branches, dedupe, contribution/payout updates, mandate trigger."""

import json

import pytest
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.engine import rounds, state
from app.engine import webhooks as wh
from app.models import WebhookEvent

HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-TEST",
    "PAYPAL-TRANSMISSION-ID": "tx-test",
    "PAYPAL-TRANSMISSION-SIG": "sig-test",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-23T10:00:00Z",
}


def capture_event(capture_id: str, event_id: str = "WH-EVT-1", *, net: str | None = "192.53") -> bytes:
    resource = {
        "id": capture_id,
        "status": "COMPLETED",
        "amount": {"currency_code": "USD", "value": "200.00"},
    }
    if net is not None:
        resource["seller_receivable_breakdown"] = {
            "gross_amount": {"currency_code": "USD", "value": "200.00"},
            "paypal_fee": {"currency_code": "USD", "value": "7.47"},
            "net_amount": {"currency_code": "USD", "value": net},
        }
    return json.dumps(
        {
            "id": event_id,
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource_type": "capture",
            "resource": resource,
        }
    ).encode()


@pytest.fixture
def verify(monkeypatch):
    """Control B1's verify_signature without the network; records the calls."""
    calls: list[dict] = []
    answer = {"ok": True}

    async def _verify(headers, body, webhook_id):
        calls.append({"headers": dict(headers), "webhook_id": webhook_id})
        return answer["ok"]

    monkeypatch.setattr(wh, "_verify", _verify)
    answer["calls"] = calls
    return answer


async def _collect_clean(circle_id, collector) -> dict[str, str]:
    state.set_flag(circle_id, "ravi_declines", False)
    results = await rounds.collect_round(circle_id, 3, publish=collector)
    return {r["member"]: r["capture_id"] for r in results}


def _publish_for(collector):
    return lambda circle_id: collector


async def test_verified_capture_marks_contribution(vaulted_circle, paypal, collector, verify):
    caps = await _collect_clean(vaulted_circle, collector)
    collector.events.clear()
    res = await wh.handle_event(HEADERS, capture_event(caps["asha"]), publish_for=_publish_for(collector))
    assert res["ok"] is True and res["duplicate"] is False and res["verified"] is True
    assert res["handled"] is True and res["member"] == "asha" and res["round_n"] == 3
    assert verify["calls"][0]["webhook_id"] == "WH-TEST"
    assert verify["calls"][0]["headers"]["paypal-transmission-id"] == "tx-test"
    row = {c["member"]: c for c in state.board_model(vaulted_circle)["contributions"] if c["round_n"] == 3}
    assert row["asha"]["webhook_verified"] is True and row["asha"]["label"] == "Webhook verified"
    assert row["ravi"]["webhook_verified"] is False
    chip = collector.chips("asha")[0]
    assert chip["webhook_verified"] is True and chip["status"] == "COMPLETED"
    assert any("webhook verified" in t["text"] for t in collector.of_type("toast"))
    with session_scope() as db:
        stored = db.exec(select(WebhookEvent)).all()
    assert len(stored) == 1 and stored[0].verified is True and stored[0].resource_id == caps["asha"]
    assert stored[0].event_type == "PAYMENT.CAPTURE.COMPLETED"


async def test_duplicate_event_is_ignored(vaulted_circle, paypal, collector, verify):
    caps = await _collect_clean(vaulted_circle, collector)
    body = capture_event(caps["meena"], "WH-EVT-DUP")
    first = await wh.handle_event(HEADERS, body, publish_for=_publish_for(collector))
    collector.events.clear()
    second = await wh.handle_event(HEADERS, body, publish_for=_publish_for(collector))
    assert first["duplicate"] is False and second == {
        "ok": True,
        "duplicate": True,
        "event_id": "WH-EVT-DUP",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "verified": True,
    }
    assert collector.events == []
    with session_scope() as db:
        assert len(db.exec(select(WebhookEvent)).all()) == 1


async def test_unverified_signature_is_stored_but_not_trusted(vaulted_circle, paypal, collector, verify):
    caps = await _collect_clean(vaulted_circle, collector)
    verify["ok"] = False
    res = await wh.handle_event(
        HEADERS, capture_event(caps["lin"], "WH-EVT-BAD"), publish_for=_publish_for(collector)
    )
    # With a webhook id configured, a failed signature is stored for debugging but never dispatched.
    assert res["ok"] is True and res["verified"] is False and res["handled"] is False
    assert res["rejected"] == "signature verification failed"
    row = {c["member"]: c for c in state.board_model(vaulted_circle)["contributions"] if c["round_n"] == 3}
    assert row["lin"]["webhook_verified"] is False
    with session_scope() as db:
        stored = db.exec(select(WebhookEvent).where(WebhookEvent.event_id == "WH-EVT-BAD")).first()
    assert stored is not None and stored.verified is False


async def test_without_webhook_id_events_are_stored_unverified(
    vaulted_circle, paypal, collector, verify, monkeypatch
):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "")
    get_settings.cache_clear()
    caps = await _collect_clean(vaulted_circle, collector)
    res = await wh.handle_event(
        {}, capture_event(caps["dev"], "WH-EVT-NOID"), publish_for=_publish_for(collector)
    )
    assert res["verified"] is False and verify["calls"] == []  # verification never attempted
    with session_scope() as db:
        stored = db.exec(select(WebhookEvent).where(WebhookEvent.event_id == "WH-EVT-NOID")).first()
    assert stored is not None and stored.verified is False


async def test_last_verified_capture_fires_the_mandate_payout(vaulted_circle, paypal, collector, verify):
    caps = await _collect_clean(vaulted_circle, collector)
    members = list(caps)
    for i, handle in enumerate(members[:-1]):
        res = await wh.handle_event(
            HEADERS, capture_event(caps[handle], f"WH-EVT-{i}"), publish_for=_publish_for(collector)
        )
        assert res["payout"] is None
    assert paypal.payouts == []
    last = await wh.handle_event(
        HEADERS, capture_event(caps[members[-1]], "WH-EVT-LAST"), publish_for=_publish_for(collector)
    )
    assert last["payout"]["ok"] is True and last["payout"]["status"] == "SUCCESS"
    assert len(paypal.payouts) == 1
    board = state.board_model(vaulted_circle)
    assert board["rounds"][2]["status"] == "PAID"
    assert any("mandate fires" in t["text"] for t in collector.of_type("toast"))
    assert collector.of_type("payout")


async def test_unverified_last_capture_does_not_pay(vaulted_circle, paypal, collector, verify):
    caps = await _collect_clean(vaulted_circle, collector)
    verify["ok"] = False
    for i, handle in enumerate(caps):
        await wh.handle_event(
            HEADERS, capture_event(caps[handle], f"WH-EVT-U{i}"), publish_for=_publish_for(collector)
        )
    assert paypal.payouts == []
    assert state.board_model(vaulted_circle)["rounds"][2]["status"] == "COLLECTED"


async def test_capture_webhook_corrects_the_ledger_net(vaulted_circle, paypal, collector, verify):
    caps = await _collect_clean(vaulted_circle, collector)
    before = state.ledger_totals(vaulted_circle)[0]
    await wh.handle_event(
        HEADERS, capture_event(caps["asha"], "WH-EVT-NET", net="190.00"), publish_for=_publish_for(collector)
    )
    after = state.ledger_totals(vaulted_circle)[0]
    assert before - after == __import__("decimal").Decimal("2.53")
    row = {c["member"]: c for c in state.board_model(vaulted_circle)["contributions"] if c["round_n"] == 3}
    assert row["asha"]["net"] == "190.00"


async def test_capture_denied_webhook_flips_to_declined(vaulted_circle, paypal, collector, verify):
    caps = await _collect_clean(vaulted_circle, collector)
    body = json.dumps(
        {
            "id": "WH-EVT-DENIED",
            "event_type": "PAYMENT.CAPTURE.DENIED",
            "resource": {"id": caps["tomas"], "status": "DECLINED", "status_details": {"reason": "RISK"}},
        }
    ).encode()
    res = await wh.handle_event(HEADERS, body, publish_for=_publish_for(collector))
    assert res["handled"] is True
    row = {c["member"]: c for c in state.board_model(vaulted_circle)["contributions"] if c["round_n"] == 3}
    assert row["tomas"]["status"] == "DECLINED" and row["tomas"]["decline_source"] == "webhook"
    assert row["tomas"]["decline_code"] == "RISK"
    assert state.ledger_totals(vaulted_circle)[0] == __import__("decimal").Decimal("192.53") * 5


async def test_payout_item_webhook_updates_the_round(vaulted_circle, paypal_factory, collector, verify):
    paypal_factory(payout_status="PENDING")
    await _collect_clean(vaulted_circle, collector)
    res = await rounds.pay_round(vaulted_circle, 3, publish=collector)
    assert res["status"] == "PENDING"
    assert state.board_model(vaulted_circle)["rounds"][2]["status"] == "COLLECTED"
    collector.events.clear()
    body = json.dumps(
        {
            "id": "WH-EVT-PAYOUT",
            "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
            "resource": {"payout_item_id": res["item_id"], "transaction_status": "SUCCESS"},
        }
    ).encode()
    out = await wh.handle_event(HEADERS, body, publish_for=_publish_for(collector))
    assert out["handled"] is True and out["status"] == "SUCCESS"
    r3 = state.board_model(vaulted_circle)["rounds"][2]
    assert r3["status"] == "PAID" and r3["payout"]["status"] == "SUCCESS" and r3["paid_at"]
    ev = collector.of_type("payout")[0]
    assert ev["status"] == "SUCCESS" and ev["item_id"] == res["item_id"] and ev["recipient"] == "dev"
    assert collector.of_type("board")


async def test_vault_token_deleted_revokes_member(vaulted_circle, paypal, collector, verify):
    body = json.dumps(
        {
            "id": "WH-EVT-VAULT",
            "event_type": "VAULT.PAYMENT-TOKEN.DELETED",
            "resource": {"id": "FAKEVAULTRAVI"},
        }
    ).encode()
    res = await wh.handle_event(HEADERS, body, publish_for=_publish_for(collector))
    assert res["handled"] is True and res["members"] == ["ravi"]
    assert "ravi" in state.circle_state(vaulted_circle, 3).revoked
    charge = await rounds.charge_member(vaulted_circle, 3, "ravi", publish=collector)
    assert charge["ok"] is False and "revoked" in charge["denied"]


async def test_foreign_and_malformed_events(vaulted_circle, paypal, collector, verify):
    foreign = await wh.handle_event(
        HEADERS, capture_event("SOMEONE-ELSES-CAPTURE", "WH-EVT-X"), publish_for=_publish_for(collector)
    )
    assert foreign["ok"] is True and foreign["handled"] is False and foreign["reason"] == "capture not ours"
    assert (await wh.handle_event(HEADERS, b"not json"))["ok"] is False
    assert (await wh.handle_event(HEADERS, b"[1,2]"))["ok"] is False
    assert (await wh.handle_event(HEADERS, b'{"event_type": "X"}'))["error"] == "missing id or event_type"
    assert (await wh.handle_event(HEADERS, b""))["ok"] is False


async def test_default_publisher_reaches_the_owning_session(session, paypal, collector, verify):
    from fakes import vault_members

    from app.engine.events import get_event_bus

    vault_members(session.circle_id)
    caps = await _collect_clean(session.circle_id, collector)
    await wh.handle_event(HEADERS, capture_event(caps["asha"], "WH-EVT-BUS"))
    types = [e["type"] for e in get_event_bus().recent(session.sid)]
    assert "chip" in types and "toast" in types
