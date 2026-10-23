from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.charter import charter_sha256
from app.domain.mandate import Mandate, mandate_payload, sign_mandate, verify_mandate

SECRET = "unit-test-only-secret"  # a test fixture value, not a real secret
APPROVED_AT = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)


@pytest.fixture
def mandate(charter) -> Mandate:
    return Mandate(
        charter_sha256=charter_sha256(charter),
        recipients=list(charter.rotation),
        cap_gross_per_round="1200.00",
        expires=date(2027, 5, 1),
        approved_at=APPROVED_AT,
        approved_by="priya",
        user_agent="pytest",
    )


@pytest.fixture
def signed(mandate) -> Mandate:
    return sign_mandate(mandate, SECRET)


def verify(m: Mandate, secret: str = SECRET, **overrides) -> tuple[bool, str]:
    kwargs = {
        "charter_sha256": m.charter_sha256,
        "round_n": 3,
        "recipient": "dev",
        "amount": Decimal("1154.93"),
        "today": date(2026, 12, 1),
    }
    kwargs.update(overrides)
    return verify_mandate(m, secret, **kwargs)


def test_sign_sets_hex_signature_and_verifies(mandate, signed):
    assert re.fullmatch(r"[0-9a-f]{64}", signed.signature)
    assert mandate.signature == ""  # the original is untouched
    ok, reason = verify(signed)
    assert ok is True
    assert "mandate ok" in reason and "dev" in reason and "1154.93" in reason


def test_signature_is_deterministic_and_covers_every_other_field(mandate):
    assert sign_mandate(mandate, SECRET).signature == sign_mandate(mandate, SECRET).signature
    payload = mandate_payload(mandate)
    assert '"signature"' not in payload
    for field in (
        "charter_sha256",
        "recipients",
        "cap_gross_per_round",
        "expires",
        "approved_at",
        "approved_by",
        "user_agent",
    ):
        assert f'"{field}"' in payload
    assert payload.index('"approved_at"') < payload.index('"charter_sha256"')  # sorted keys
    assert '"cap_gross_per_round":"1200.00"' in payload


def test_unsigned_mandate_fails(mandate):
    ok, reason = verify(mandate)
    assert ok is False and "not signed" in reason


def test_tampered_amount_is_detected(signed):
    tampered = signed.model_copy(update={"cap_gross_per_round": Decimal("9999.00")})
    ok, reason = verify(tampered, amount=Decimal("5000.00"))
    assert ok is False and "signature" in reason


def test_tampered_recipient_is_detected(signed):
    recipients = list(signed.recipients)
    recipients[2], recipients[5] = recipients[5], recipients[2]  # ravi now "owns" round 3
    tampered = signed.model_copy(update={"recipients": recipients})
    ok, reason = verify(tampered, recipient="ravi")
    assert ok is False and "signature" in reason


def test_wrong_secret_is_detected(signed):
    ok, reason = verify(signed, secret="another-secret")
    assert ok is False and "signature" in reason


def test_charter_hash_mismatch(signed):
    ok, reason = verify(signed, charter_sha256="f" * 64)
    assert ok is False and "charter" in reason


def test_wrong_recipient_for_round(signed):
    ok, reason = verify(signed, recipient="ravi")
    assert ok is False and "dev" in reason and "ravi" in reason
    assert verify(signed, round_n=6, recipient="ravi")[0] is True


def test_amount_cap(signed):
    assert verify(signed, amount="1200.00")[0] is True
    ok, reason = verify(signed, amount="1200.01")
    assert ok is False and "1200.01" in reason and "cap 1200.00" in reason
    assert verify(signed, amount="0.00")[0] is False


def test_expiry(signed):
    assert verify(signed, today=date(2027, 5, 1))[0] is True
    ok, reason = verify(signed, today=date(2027, 5, 2))
    assert ok is False and "expired on 2027-05-01" in reason


def test_round_out_of_range(signed):
    ok, reason = verify(signed, round_n=7, recipient="ravi")
    assert ok is False and "round 7" in reason


def test_json_round_trip_still_verifies(signed):
    stored = signed.model_dump_json()
    loaded = Mandate.model_validate_json(stored)
    assert loaded == signed
    assert verify(loaded)[0] is True


def test_naive_and_aware_approved_at_sign_identically(mandate):
    naive = mandate.model_copy(update={"approved_at": APPROVED_AT.replace(tzinfo=None)})
    ist = mandate.model_copy(
        update={"approved_at": APPROVED_AT.astimezone(timezone(timedelta(hours=5, minutes=30)))}
    )
    assert sign_mandate(naive, SECRET).signature == sign_mandate(mandate, SECRET).signature
    assert sign_mandate(ist, SECRET).signature == sign_mandate(mandate, SECRET).signature
    assert Mandate.model_validate(naive.model_dump()).approved_at.tzinfo is not None


def test_empty_secret_is_refused(mandate, signed):
    with pytest.raises(ValueError, match="secret"):
        sign_mandate(mandate, "")
    ok, reason = verify(signed, secret="")
    assert ok is False and "secret" in reason
