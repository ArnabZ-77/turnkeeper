"""Recovery engine (B6): decline -> reply -> commitment -> proposal -> consents/votes/approval -> execution ->
rotation, invoice, payout -> INVOICING.INVOICE.PAID -> advance repaid. PayPal and the toolkit are faked."""

import json
from decimal import Decimal

import pytest
from recovery_fakes import (
    FAKE_INVOICE_ID,
    SWAPPABLE_ROTATION,
    FakeInvoiceToolkit,
    invoice_paid_event,
    use_swappable_rotation,
)
from sqlmodel import select

from app.db import session_scope
from app.engine import inbox, recovery, rounds, state, webhooks
from app.models import Contribution, LedgerRow, MandateRow

SWAPPED = ["meena", "asha", "dev", "lin", "tomas", "ravi"]


@pytest.fixture
def toolkit(monkeypatch) -> FakeInvoiceToolkit:
    tk = FakeInvoiceToolkit()
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    return tk


@pytest.fixture
async def declined(vaulted_circle, paypal, collector):
    """Round 3 collected with ravi declined (fixture flag), on the swappable rotation."""
    use_swappable_rotation(vaulted_circle)
    await rounds.collect_round(vaulted_circle, 3, publish=collector)
    return vaulted_circle


def _contrib(circle_id, member, round_n=3) -> Contribution:
    with session_scope() as db:
        return db.exec(
            select(Contribution).where(
                Contribution.circle_id == circle_id,
                Contribution.round_n == round_n,
                Contribution.member_handle == member,
            )
        ).first()


def _ledger(circle_id, kind):
    return [r for r in state.ledger_rows(circle_id) if r.kind == kind]


async def test_find_shortfall_and_rules_proposal(declined, collector, toolkit):
    cid = declined
    assert recovery.find_shortfall(cid) == (3, "ravi")
    inbox.add_message(cid, "ravi", inbox.DEFAULT_RAVI_REPLY, lang="hi", direction="in", source="judge")
    out = await recovery.run_rules_recovery(cid, publish=collector)
    assert out["ok"] is True
    assert out["commitment"]["date"] == "2027-01-01" and out["commitment"]["amount"] == "200.00"
    proposal = out["proposal"]
    assert [r["type"] for r in proposal["remedies"]] == ["buffer_advance", "repay_by_invoice", "turn_swap"]
    assert proposal["remedies"][2]["params"]["swap_with"] == "tomas"
    assert proposal["remedies"][1]["params"]["due_date"] == "2027-01-01"
    vote = out["vote"]
    assert (
        vote["status"] == "voting"
        and vote["votes"]["needed"] == 4
        and vote["consents"]["needed"] == ["ravi", "tomas"]
    )
    # the treasurer asked ravi in Hindi first
    outbound = inbox.list_messages(cid, member="ravi", direction="out")
    assert outbound and outbound[0]["lang"] == "hi"
    view = recovery.recovery_view(cid)
    assert view["status"] == "voting" and view["approval"]["status"] == "requested"
    assert toolkit.calls == []  # nothing moved yet


async def test_execution_is_refused_without_quorum_consents_and_approval(declined, collector, toolkit):
    cid = declined
    out = await recovery.run_rules_recovery(cid, publish=collector)
    sid = out["set_id"]
    denied = await recovery.execute_remedies(cid, sid, publish=collector)
    assert denied["ok"] is False and "execution needs" in denied["denied"]
    early = await recovery.approve(cid, sid, publish=collector)
    assert early["ok"] is False and "yes votes" in early["denied"] and "consent missing" in early["denied"]
    for h in ("ravi", "tomas"):
        await recovery.record_consent(cid, sid, h, "yes", publish=collector)
    for h in ("asha", "meena", "lin"):
        await recovery.cast_vote(cid, sid, h, "yes", publish=collector)
    still = await recovery.approve(cid, sid, publish=collector)
    assert still["ok"] is False and "3/4 yes votes" in still["denied"]
    swap_only = await recovery.reschedule_from_set(cid, sid, publish=collector)
    assert swap_only["ok"] is False
    assert _ledger(cid, "buffer_advance") == [] and toolkit.calls == []
    assert state.board_model(cid)["circle"]["rotation"] == SWAPPABLE_ROTATION


async def test_full_recovery_flow(declined, collector, toolkit, paypal, monkeypatch):
    cid = declined
    inbox.add_message(cid, "ravi", inbox.DEFAULT_RAVI_REPLY, lang="hi", direction="in", source="judge")
    sid = (await recovery.run_rules_recovery(cid, publish=collector))["set_id"]
    for h in ("ravi", "tomas"):
        await recovery.record_consent(cid, sid, h, "yes", publish=collector)
    for h in ("asha", "meena", "lin", "dev"):
        status = await recovery.cast_vote(cid, sid, h, "yes", publish=collector)
    assert status["votes"]["passed"] is True and status["blockers"] == ["organizer approval missing"]

    result = await recovery.approve(cid, sid, publish=collector)
    assert result["ok"] is True and result["status"] == "executed"
    kinds = [r["type"] for r in result["results"]]
    assert kinds == ["buffer_advance", "turn_swap", "repay_by_invoice"]

    # buffer advance: ledger + COVERED chip + counter
    adv = _ledger(cid, "buffer_advance")
    assert [(r.amount, r.member_handle, r.round_n) for r in adv] == [("192.53", "ravi", 3)]
    c = _contrib(cid, "ravi")
    assert c.status == "COVERED" and c.label == "Covered by buffer"
    board = state.board_model(cid)
    assert board["counters"]["shortfalls_covered"] == 1

    # turn swap: rotation override, recipients, member positions, schedule_changed, re-signed mandate
    assert board["circle"]["rotation"] == SWAPPED
    assert [m["handle"] for m in board["members"]] == SWAPPED
    assert json.loads(state.load_circle(cid).rotation_json) == SWAPPED
    assert collector.of_type("schedule_changed")[-1]["rotation"] == SWAPPED
    with session_scope() as db:
        mandates = db.exec(
            select(MandateRow).where(MandateRow.circle_id == cid).order_by(MandateRow.id)
        ).all()
    assert json.loads(mandates[-1].json)["recipients"] == SWAPPED and len(mandates) == 2

    # invoice through the toolkit: create -> send -> QR, idempotency keys, recipient email
    assert toolkit.methods() == ["create_invoice", "send_invoice", "generate_invoice_qr_code"]
    create_params = toolkit.calls[0][1]
    assert create_params["items"][0]["unit_amount"]["value"] == "200.00"
    assert create_params["primary_recipients"][0]["billing_info"]["email_address"] == "ravi@example.com"
    assert create_params["invoice_number"].startswith("TK-") and create_params["invoice_number"].endswith(
        "-3-ravi"
    )
    assert [c[2].rsplit(":", 1)[-1] for c in toolkit.calls] == ["inv", "invsend", "invqr"]
    assert toolkit.calls[1][1] == {
        "invoice_id": FAKE_INVOICE_ID,
        "send_to_recipient": True,
        "note": "Invoice due Fri 2027-01-01",
    }
    inv = board["recovery"]["invoice"]
    assert inv["id"] == FAKE_INVOICE_ID and inv["status"] == "SENT" and inv["qr"].endswith("/recovery/qr")
    assert inv["simulated"] is False
    assert recovery.recovery_view(cid, with_qr=True)["invoice"]["qr_data"].startswith(
        "data:image/png;base64,"
    )
    labels = {m["handle"]: m["label"] for m in board["members"]}
    assert "Round 3: covered by buffer" in labels["ravi"] and "Invoice due Fri" in labels["ravi"]
    assert labels["tomas"] == "Turn moved to round 5 (swap with ravi)"

    # the pot was paid through the usual gates (mandate re-signed for the new order; dev is round 3)
    payout = result["payout"]
    assert payout["ok"] is True and payout["recipient"] == "dev"
    assert Decimal(payout["amount"]) == Decimal("192.53") * 6 - Decimal("0.25")
    assert len(paypal.payouts) == 1

    # INVOICING.INVOICE.PAID webhook (verified) -> advance repaid, buffer restored, label
    async def _verified(*_a, **_k):
        return True

    monkeypatch.setattr(webhooks, "_verify", _verified)
    body = json.dumps(invoice_paid_event()).encode()
    res = await webhooks.handle_event({}, body, publish_for=lambda _cid: collector)
    assert res["handled"] is True and res["repaid"] == "192.53" and res["simulated"] is False
    assert [(r.amount, r.ref) for r in _ledger(cid, "advance_repaid")] == [("192.53", FAKE_INVOICE_ID)]
    assert state.ledger_totals(cid)[1] == Decimal("250.00")
    assert _contrib(cid, "ravi").label == "Round 3: covered, not missed"
    board = state.board_model(cid)
    assert board["recovery"]["status"] == "repaid" and board["recovery"]["invoice"]["status"] == "PAID"
    assert board["members"][-1]["label"].startswith("Round 3: covered, not missed")
    dup = await webhooks.handle_event({}, body, publish_for=lambda _cid: collector)
    assert dup["duplicate"] is True and len(_ledger(cid, "advance_repaid")) == 1

    history = state.member_history(cid, "ravi")
    assert history["commitments"][0]["date"] == "2027-01-01"
    assert any(inbox.DEFAULT_RAVI_REPLY in r["text"] for r in history["replies"])


async def test_invoice_failure_keeps_the_advance_and_marks_the_remedy(
    declined, collector, monkeypatch, paypal
):
    tk = FakeInvoiceToolkit(fail_on="send_invoice")
    monkeypatch.setattr(recovery, "_toolkit", lambda: tk)
    cid = declined
    sid = (await recovery.run_rules_recovery(cid, publish=collector))["set_id"]
    for h in ("ravi", "tomas"):
        await recovery.record_consent(cid, sid, h, "yes")
    for h in ("asha", "meena", "lin", "dev"):
        await recovery.cast_vote(cid, sid, h, "yes")
    result = await recovery.approve(cid, sid, publish=collector)
    assert result["ok"] is True
    invoice = [r for r in result["results"] if r["type"] == "repay_by_invoice"][0]
    assert invoice["ok"] is False and invoice["error"] == "UNPROCESSABLE_ENTITY"
    view = recovery.recovery_view(cid)
    assert view["invoice"]["status"] == "DRAFT"
    assert [r["status"] for r in view["remedies"]] == ["executed", "failed", "executed"]
    assert _contrib(cid, "ravi").status == "COVERED"


async def test_fixture_default_proposal_swaps_ravi_with_tomas(vaulted_circle, paypal, collector, toolkit):
    cid = vaulted_circle  # the fixture rotation itself (ravi 5th, tomas 6th), no rewrite
    await rounds.collect_round(cid, 3, publish=collector)
    out = await recovery.propose(cid, None, None, None, publish=collector)
    assert out["ok"] is True and [r["type"] for r in out["remedies"]] == [
        "buffer_advance",
        "repay_by_invoice",
        "turn_swap",
    ]
    swap = [r for r in out["remedies"] if r["type"] == "turn_swap"][0]
    assert swap["params"]["swap_with"] == "tomas"


async def test_ravi_last_rotation_means_the_default_has_no_swap(vaulted_circle, paypal, collector, toolkit):
    cid = vaulted_circle
    use_swappable_rotation(cid, ["meena", "asha", "dev", "lin", "tomas", "ravi"])
    await rounds.collect_round(cid, 3, publish=collector)
    out = await recovery.propose(cid, None, None, None, publish=collector)
    assert out["ok"] is True and [r["type"] for r in out["remedies"]] == [
        "buffer_advance",
        "repay_by_invoice",
    ]
    bad = await recovery.propose(
        cid,
        3,
        "ravi",
        [
            {"type": "buffer_advance", "amount": "192.53", "reason": "x"},
            {"type": "turn_swap", "swap_with": "tomas", "reason": "y"},
        ],
        source="agent",
    )
    assert bad["ok"] is False and any("may only move later" in p for p in bad["problems"])
    assert recovery.latest_set(cid).status == "invalid"
    vote = await recovery.open_vote(cid, bad["set_id"])
    assert vote["ok"] is False and "invalid proposal" in vote["denied"]


async def test_record_commitment_validation(vaulted_circle):
    cid = vaulted_circle
    assert recovery.record_commitment(cid, "ravi", "200.00", "2027-01-01")["ok"] is True
    assert recovery.record_commitment(cid, "ravi", "999.00", "2027-01-01")["error"] == "INVALID_INPUT"
    assert recovery.record_commitment(cid, "ravi", "200.00", "2026-01-01")["error"] == "INVALID_INPUT"
    assert recovery.record_commitment(cid, "nobody", "200.00", "2027-01-01")["error"] == "INVALID_INPUT"
    assert recovery.record_commitment(cid, "ravi", "abc", "2027-01-01")["error"] == "INVALID_INPUT"


def test_parse_payday():
    from datetime import date

    today = date(2026, 12, 29)
    assert inbox.parse_payday(inbox.DEFAULT_RAVI_REPLY, today=today) == date(2027, 1, 1)
    assert inbox.parse_payday("I get paid on Friday, will pay then", today=today) == date(2027, 1, 1)
    assert inbox.parse_payday("pay $5000 on 2027-01-05 please", today=today) == date(2027, 1, 5)
    assert inbox.parse_payday("tomorrow", today=today) == date(2026, 12, 30)
    assert inbox.parse_payday("sorry", today=today) is None


async def test_simulated_invoice_paid_without_an_invoice_is_refused(vaulted_circle, collector):
    with pytest.raises(recovery.RecoveryError):
        await recovery.simulate_invoice_paid(vaulted_circle, publish=collector)


async def test_unknown_invoice_webhook_is_not_ours(vaulted_circle, monkeypatch, collector):
    async def _verified(*_a, **_k):
        return True

    monkeypatch.setattr(webhooks, "_verify", _verified)
    res = await webhooks.handle_event(
        {}, json.dumps(invoice_paid_event("INV2-NOT-OURS")).encode(), publish_for=lambda _c: collector
    )
    assert res["handled"] is False and res["reason"] == "invoice not ours"
    assert state.ledger_totals(vaulted_circle)[1] == Decimal("250.00")
    assert [r for r in state.ledger_rows(vaulted_circle) if r.kind == "advance_repaid"] == []
    assert LedgerRow  # imported for the ledger kinds above
