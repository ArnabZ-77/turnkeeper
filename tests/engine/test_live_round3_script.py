"""scripts/live_round3.py against the faked sandbox: JSON lines with ids, PASS/FAIL, exit codes, no secrets."""

from __future__ import annotations

import json

from fakes import vault_members
from recovery_fakes import FAKE_INVOICE_ID, FakeInvoiceToolkit

from app.engine import recovery
from scripts import live_round3


def _lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


async def test_live_round3_runs_the_demo_in_process(paypal, monkeypatch, capsys):
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)

    async def no_sleep(_s: float) -> None:
        return None

    ok = await live_round3.run_live(
        live_round3.parse_args(["--poll", "0"]), after_create=vault_members, sleep=no_sleep
    )
    assert ok is True
    out = capsys.readouterr().out
    lines = _lines(out)
    steps = [line["step"] for line in lines]
    assert steps[:2] == ["config", "reset"]
    for step in ("charge", "confirm", "reply", "recovery", "vote", "remedy", "payout", "summary"):
        assert step in steps, step
    charges = {line["member"]: line for line in lines if line["step"] == "charge"}
    assert (
        charges["ravi"]["status"] == "DECLINED" and charges["ravi"]["decline_code"] == "INSTRUMENT_DECLINED"
    )
    assert all(c["ok"] for c in charges.values())
    assert charges["asha"]["order_id"].startswith("FAKEORD") and charges["asha"]["capture_id"].startswith(
        "FAKECAP"
    )
    assert all(line["ok"] for line in lines if line["step"] == "confirm")
    assert [line for line in lines if line["step"] == "recovery"][0]["mode"] == "rules"
    remedies = {line["type"]: line for line in lines if line["step"] == "remedy"}
    assert remedies["repay_by_invoice"]["invoice_id"] == FAKE_INVOICE_ID
    assert set(remedies) == {"buffer_advance", "turn_swap", "repay_by_invoice"}
    payout = [line for line in lines if line["step"] == "payout"][0]
    assert payout["ok"] and payout["batch_id"] == "FAKEBATCH001" and payout["item_id"] == "FAKEITEM001"
    assert payout["status"] == "SUCCESS" and payout["recipient"] == "dev"
    summary = lines[-1]
    assert summary["step"] == "summary" and summary["ok"] is True and summary["payout_status"] == "SUCCESS"
    assert "@" not in out and "test-client-secret" not in out and "test-client-id" not in out
    assert [m for m in tk.methods()] == ["create_invoice", "send_invoice", "generate_invoice_qr_code"]


def test_live_round3_fails_without_seeded_vault_ids(capsys):
    assert live_round3.main(["--poll", "0"]) == 1
    out = capsys.readouterr().out
    assert out.strip().endswith("FAIL")
    reset = [line for line in _lines(out) if line["step"] == "reset"][0]
    assert reset["ok"] is False and reset["vault_seeded"] is False
    assert "TK_SEED_VAULT_IDS" in [line for line in _lines(out) if line["step"] == "result"][0]["reason"]


def test_live_round3_fails_without_credentials(monkeypatch, capsys):
    from app.config import get_settings

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "")
    get_settings.cache_clear()
    assert live_round3.main([]) == 1
    out = capsys.readouterr().out
    assert "credentials not configured" in out and out.strip().endswith("FAIL")
