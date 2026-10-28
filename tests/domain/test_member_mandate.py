"""Member mandates (pure): sign, verify, expiry, hash mismatch, tamper detection, defaults and honest labels."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain import member_mandate as mm
from app.domain.charter import charter_sha256
from app.domain.mandate import hmac_hex

SECRET = "unit-test-only-secret"  # a test fixture value, not a real secret
APPROVED_AT = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
TODAY = date(2026, 12, 1)
CIRCLE = "abc123"


@pytest.fixture
def mandate(charter) -> mm.MemberMandate:
    return mm.default_member_mandate(
        charter,
        "tomas",
        circle_id=CIRCLE,
        charter_sha256=charter_sha256(charter),
        approved_at=APPROVED_AT,
        expires=date(2027, 6, 1),
    )


@pytest.fixture
def signed(mandate) -> mm.MemberMandate:
    return mm.sign_member_mandate(mandate, SECRET)


def verify(m: mm.MemberMandate, charter, secret: str = SECRET, **over):
    kw = {
        "circle_id": CIRCLE,
        "member": "tomas",
        "charter_sha256": charter_sha256(charter),
        "today": TODAY,
    }
    kw.update(over)
    return mm.verify_member_mandate(m, secret, **kw)


def test_labels_are_honest():
    assert "AP2-style Intent Mandate, our own implementation" in mm.MANDATE_LABEL
    assert "PayPal AP2 APIs are not public" in mm.MANDATE_LABEL
    assert "we do not claim AP2 support" in mm.MANDATE_LABEL
    assert mm.SIGNATURE_LABEL == "server-attested HMAC signature, not a key held by the member"


def test_sign_sets_hex_signature_and_leaves_original(mandate, signed):
    assert re.fullmatch(r"[0-9a-f]{64}", signed.signature)
    assert mandate.signature == ""


def test_signature_is_deterministic(mandate):
    assert (
        mm.sign_member_mandate(mandate, SECRET).signature == mm.sign_member_mandate(mandate, SECRET).signature
    )


def test_signature_reuses_the_canonical_hmac_helper(mandate, signed):
    assert signed.signature == hmac_hex(mm.member_mandate_payload(mandate), SECRET)


def test_payload_excludes_signature_and_is_canonical(signed):
    payload = mm.member_mandate_payload(signed)
    assert '"signature"' not in payload
    assert payload == mm.member_mandate_payload(signed.model_copy(update={"signature": ""}))
    assert payload.startswith("{") and '": ' not in payload  # compact separators


def test_sign_needs_a_secret(mandate):
    with pytest.raises(ValueError):
        mm.sign_member_mandate(mandate, "")


def test_verify_ok(signed, charter):
    ok, reason = verify(signed, charter)
    assert ok is True and "mandate ok" in reason and "tomas" in reason


def test_verify_within_the_limit_and_at_the_limit(signed, charter):
    assert verify(signed, charter, amount="199.99")[0] is True
    assert verify(signed, charter, amount="200.00")[0] is True


def test_verify_over_the_limit(signed, charter):
    ok, reason = verify(signed, charter, amount="230.00")
    assert ok is False and "230.00" in reason and "200.00" in reason


def test_verify_rejects_non_positive_amount(signed, charter):
    assert verify(signed, charter, amount="0")[0] is False


def test_verify_unsigned(mandate, charter):
    ok, reason = verify(mandate, charter)
    assert ok is False and "not signed" in reason


def test_verify_without_a_secret(signed, charter):
    ok, reason = verify(signed, charter, secret="")
    assert ok is False and "secret" in reason


def test_verify_wrong_secret(signed, charter):
    ok, reason = verify(signed, charter, secret="another-secret")
    assert ok is False and "does not verify" in reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_charge_per_round", Decimal("999.00")),
        ("max_advance", Decimal("999.00")),
        ("accept_earlier_swap", False),
        ("require_ask_for_later_swap", False),
        ("expires", date(2030, 1, 1)),
        ("approved_by", "someone-else"),
        ("charter_sha256", "f" * 64),
        ("circle_id", "zzz999"),
        ("member", "ravi"),
    ],
)
def test_tampering_with_any_field_breaks_the_signature(signed, charter, field, value):
    forged = signed.model_copy(update={field: value})
    ok, reason = verify(forged, charter)
    assert ok is False and "does not verify" in reason


def test_expiry_is_inclusive_of_the_last_day(signed, charter):
    assert verify(signed, charter, today=date(2027, 6, 1))[0] is True
    ok, reason = verify(signed, charter, today=date(2027, 6, 2))
    assert ok is False and "expired on 2027-06-01" in reason


def test_charter_hash_mismatch(signed, charter):
    ok, reason = verify(signed, charter, charter_sha256="e" * 64)
    assert ok is False and "charter" in reason and "eeeeeeee" in reason


def test_wrong_circle_and_wrong_member(signed, charter):
    assert "circle" in verify(signed, charter, circle_id="other1")[1]
    ok, reason = verify(signed, charter, member="ravi")
    assert ok is False and "approved for tomas" in reason


def test_timezone_aware_approval_is_stored_as_utc(charter):
    plus5 = APPROVED_AT.astimezone(timezone(timedelta(hours=5)))
    m = mm.default_member_mandate(
        charter,
        "tomas",
        circle_id=CIRCLE,
        charter_sha256="a" * 64,
        approved_at=plus5,
        expires=date(2027, 1, 1),
    )
    assert m.approved_at.utcoffset() == timedelta(0) and m.approved_at == APPROVED_AT


def test_model_is_frozen_and_validates():
    with pytest.raises(ValidationError):
        mm.MemberMandate(
            member="tomas",
            circle_id=CIRCLE,
            charter_sha256="a" * 64,
            max_charge_per_round="0",
            expires=date(2027, 1, 1),
            approved_at=APPROVED_AT,
            approved_by="x",
            max_advance="0",
        )


def test_model_is_frozen(signed):
    with pytest.raises(ValidationError):
        signed.max_charge_per_round = Decimal("1")  # type: ignore[misc]


def test_json_round_trip_keeps_the_signature_valid(signed, charter):
    again = mm.MemberMandate.model_validate_json(signed.model_dump_json())
    assert again == signed and verify(again, charter)[0] is True


def test_default_mandate_rules_and_summary(mandate):
    assert mandate.max_charge_per_round == Decimal("200.00") and mandate.max_advance == Decimal("200.00")
    assert mandate.accept_earlier_swap and mandate.require_ask_for_later_swap
    assert mandate.rule_summary == "accepts earlier turns, asks for later ones" == mm.TOMAS_RULE
    assert mandate.scheme == mm.SCHEME and mandate.attestation == "server-hmac-sha256"


def test_default_mandate_rejects_unknown_member(charter):
    with pytest.raises(ValueError):
        mm.default_member_mandate(
            charter,
            "nobody",
            circle_id=CIRCLE,
            charter_sha256="a" * 64,
            approved_at=APPROVED_AT,
            expires=TODAY,
        )


def test_default_mandates_cover_the_six_demo_members_and_all_verify(charter):
    ms = mm.default_member_mandates(
        charter,
        circle_id=CIRCLE,
        charter_sha256=charter_sha256(charter),
        approved_at=APPROVED_AT,
        expires=date(2027, 6, 1),
        secret=SECRET,
    )
    assert [m.member for m in ms] == [m.handle for m in charter.members] and len(ms) == 6
    for m in ms:
        assert verify(m, charter, member=m.member, amount="200.00")[0] is True


def test_default_mandates_can_be_limited_to_some_members(charter):
    ms = mm.default_member_mandates(
        charter,
        circle_id=CIRCLE,
        charter_sha256="a" * 64,
        approved_at=APPROVED_AT,
        expires=TODAY,
        secret=SECRET,
        members=["ravi", "tomas"],
    )
    assert [m.member for m in ms] == ["ravi", "tomas"]  # charter order (ravi is listed before tomas)


def test_policy_entry_shapes(signed, charter):
    kw = {"circle_id": CIRCLE, "member": "tomas", "charter_sha256": charter_sha256(charter), "today": TODAY}
    ok = mm.policy_entry(signed, SECRET, **kw)
    assert ok["ok"] is True and ok["max_charge_per_round"] == "200.00"
    missing = mm.policy_entry(None, SECRET, **kw)
    assert missing == {
        "ok": False,
        "reason": "no member mandate on file for tomas",
        "max_charge_per_round": None,
    }
    bad = mm.policy_entry(signed, "wrong", **kw)
    assert bad["ok"] is False and bad["max_charge_per_round"] == "200.00"
    expired = mm.policy_entry(signed, SECRET, **{**kw, "today": date(2028, 1, 1)})
    assert expired["ok"] is False and "expired" in expired["reason"]
