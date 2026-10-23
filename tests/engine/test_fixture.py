"""Fixture load, collateral-valid rotation and the shape of a fresh clone."""

import json

from sqlmodel import select

from app.db import session_scope
from app.domain.charter import Charter, charter_sha256
from app.engine import state
from app.engine.fixture import (
    VAULT_PLACEHOLDER_PREFIX,
    clone_fixture,
    fixture_charter,
    is_placeholder_vault,
    load_fixture,
    seed_overrides,
)
from app.models import Circle, Contribution, LedgerRow, MandateRow, MemberRow, Round

HANDLES = {"asha", "ravi", "meena", "tomas", "lin", "dev"}


def test_fixture_charter_validates_and_matches_the_plan():
    charter = fixture_charter()
    assert isinstance(charter, Charter)
    assert charter.name == "Temple Circle A"
    assert str(charter.amount) == "200.00"
    assert charter.cadence == "monthly"
    assert charter.start_date.isoformat() == "2026-11-01"
    assert {m.handle for m in charter.members} == HANDLES
    assert set(charter.rotation) == HANDLES and len(charter.rotation) == 6
    assert str(charter.buffer.seed) == "250.00"
    assert charter.quorum == "4/6"


def test_rotation_satisfies_the_collateral_rule():
    charter = fixture_charter()
    track = {m.handle: m.track_record_rounds for m in charter.members}
    seen_zero = False
    for handle in charter.rotation:
        if track[handle] == 0:
            seen_zero = True
        else:
            assert not seen_zero, f"{handle} (track record) follows a no-track-record member"
    assert charter.rotation[:2] == ["meena", "asha"]


def test_clone_creates_members_rounds_replay_and_mandate(circle_id):
    circle = state.load_circle(circle_id)
    assert circle.is_fixture_clone is True
    assert len(circle.id) == 6 and len(circle.run_id) == 8
    assert circle.charter_sha256 == charter_sha256(state.charter_of(circle))

    with session_scope() as db:
        members = db.exec(select(MemberRow).where(MemberRow.circle_id == circle_id)).all()
        rounds = db.exec(select(Round).where(Round.circle_id == circle_id).order_by(Round.n)).all()
        contribs = db.exec(select(Contribution).where(Contribution.circle_id == circle_id)).all()
        mandates = db.exec(select(MandateRow).where(MandateRow.circle_id == circle_id)).all()

    assert {m.handle for m in members} == HANDLES
    assert all(m.vault_id.startswith(VAULT_PLACEHOLDER_PREFIX) for m in members)
    assert sorted(m.rotation_pos for m in members) == [1, 2, 3, 4, 5, 6]
    assert [r.n for r in rounds] == [1, 2, 3, 4, 5, 6]
    assert [r.recipient_handle for r in rounds] == list(state.charter_of(circle).rotation)

    paid = [r for r in rounds if r.n in (1, 2)]
    assert all(r.status == "PAID" for r in paid)
    assert all(r.payout_status == "SUCCESS" for r in paid)
    assert all(
        r.payout_item_id.startswith("REPLAY-") and r.payout_batch_id.startswith("REPLAY-") for r in paid
    )
    assert all(r.paid_at is not None and r.last_capture_at is not None for r in paid)
    assert all(r.status == "PENDING" and r.payout_item_id is None for r in rounds if r.n >= 3)

    replayed = [c for c in contribs if c.round_n in (1, 2)]
    assert len(replayed) == 12
    assert all(c.status == "COMPLETED" for c in replayed)
    # M1 review: replayed rows are never shown as webhook-verified.
    assert not any(c.webhook_verified for c in replayed)
    assert all(c.order_id.startswith("REPLAY-") and "replay" in c.label for c in replayed)
    assert all(c.order_id.startswith("REPLAY-") and c.capture_id.startswith("REPLAY-") for c in replayed)
    pending = [c for c in contribs if c.round_n >= 3]
    assert len(pending) == 24 and all(c.status == "PENDING" and c.attempt == 0 for c in pending)
    predicted = {(c.round_n, c.member_handle) for c in pending if c.predicted}
    assert predicted == {(3, "ravi")}
    assert len(mandates) == 1


def test_clone_ledger_is_balanced_after_the_replay(circle_id):
    circle_available, buffer_balance = state.ledger_totals(circle_id)
    assert str(buffer_balance) == "250.00"
    assert str(circle_available) == "0.00"
    with session_scope() as db:
        kinds = [r.kind for r in db.exec(select(LedgerRow).where(LedgerRow.circle_id == circle_id)).all()]
    assert kinds.count("buffer_seed") == 1
    assert kinds.count("contribution_net") == 12
    assert kinds.count("payout") == 2 and kinds.count("payout_fee") == 2


def test_clone_flags_counters_and_clock(circle_id):
    circle = state.load_circle(circle_id)
    assert state.get_flags(circle) == {"ravi_declines": True, "next_payout_unclaimed": False}
    assert state.get_counters(circle) == {
        "organizer_messages_typed": 0,
        "rounds_paid_on_time": 2,
        "shortfalls_covered": 0,
    }
    assert circle.virtual_now.isoformat().startswith("2026-12-29T09:00:00")


def test_seed_overrides_replace_placeholders(monkeypatch):
    seed = {
        "ravi": "FAKEVAULTRAVI",
        "asha": {"vault_id": "FAKEVAULTASHA", "payout_receiver": "asha@example.com"},
    }
    monkeypatch.setenv("TK_SEED_VAULT_IDS", json.dumps(seed))
    assert seed_overrides()["ravi"] == {"vault_id": "FAKEVAULTRAVI"}
    with session_scope() as db:
        circle = clone_fixture(db, circle_id="seed01", run_id="abcdefgh")
        db.commit()
        rows = {m.handle: m for m in db.exec(select(MemberRow).where(MemberRow.circle_id == circle.id)).all()}
    assert rows["ravi"].vault_id == "FAKEVAULTRAVI"
    assert rows["asha"].vault_id == "FAKEVAULTASHA" and rows["asha"].payout_receiver == "asha@example.com"
    assert is_placeholder_vault(rows["meena"].vault_id)
    assert not is_placeholder_vault(rows["ravi"].vault_id)


def test_seed_overrides_ignore_garbage(monkeypatch):
    monkeypatch.setenv("TK_SEED_VAULT_IDS", "not json")
    assert seed_overrides() == {}
    monkeypatch.setenv("TK_SEED_VAULT_IDS", "[1, 2]")
    assert seed_overrides() == {}


def test_fixture_file_has_no_real_ids():
    text = json.dumps(load_fixture())
    assert "VAULT_ID_" in text
    for needle in ("@paypal.com", "sb-", "A21AA", "EC-"):
        assert needle not in text


def test_two_clones_are_independent_rows():
    with session_scope() as db:
        a = clone_fixture(db, circle_id="aaaaaa", run_id="runaaaaa")
        b = clone_fixture(db, circle_id="bbbbbb", run_id="runbbbbb")
        db.commit()
        circles = db.exec(select(Circle)).all()
    assert {c.id for c in circles} >= {"aaaaaa", "bbbbbb"}
    assert a.run_id != b.run_id and a.charter_sha256 == b.charter_sha256
