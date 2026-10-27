"""Organizer-approved payout mandate, server-signed with HMAC-SHA256. Pure logic.

The organizer approves once: "pay these recipients in this order, at most this much per round, until this
date, for exactly this charter". The server signs the canonical JSON of those fields (every field except
``signature``) with a secret the caller passes in (``get_settings().mandate_hmac_secret``); this module
never reads settings. ``verify_mandate`` re-checks the signature and the five facts before a payout.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.charter import canonical_dumps, to_utc
from app.domain.fees import MoneyInput, PositiveMoney, money


class Mandate(BaseModel):
    model_config = ConfigDict(frozen=True)

    charter_sha256: str = Field(min_length=1)
    recipients: list[str] = Field(min_length=1)  # payout order, one handle per round
    cap_gross_per_round: PositiveMoney
    expires: date
    approved_at: datetime  # stored as UTC
    approved_by: str = Field(min_length=1)
    user_agent: str = ""
    signature: str = ""  # hex HMAC-SHA256, set by sign_mandate

    @field_validator("approved_at")
    @classmethod
    def _approved_at_utc(cls, v: datetime) -> datetime:
        return to_utc(v)


def mandate_payload(m: Mandate) -> str:
    """The signed bytes: canonical JSON of every field except ``signature``."""
    return canonical_dumps(m.model_dump(mode="python", exclude={"signature"}))


def _hmac_hex(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def hmac_hex(payload: str, secret: str) -> str:
    """The shared signing primitive (hex HMAC-SHA256); ``app.domain.member_mandate`` reuses it."""
    return _hmac_hex(payload, secret)


def sign_mandate(m: Mandate, secret: str) -> Mandate:
    """A copy of ``m`` with ``signature`` = hex HMAC-SHA256(secret, canonical non-signature fields)."""
    if not secret:
        raise ValueError("mandate secret must not be empty")
    return m.model_copy(update={"signature": _hmac_hex(mandate_payload(m), secret)})


def verify_mandate(
    m: Mandate,
    secret: str,
    *,
    charter_sha256: str,
    round_n: int,
    recipient: str,
    amount: MoneyInput,
    today: date,
) -> tuple[bool, str]:
    """``(ok, reason)``: signature, charter hash, recipient for the round, amount <= cap, not expired."""
    if not secret:
        return False, "mandate secret is not configured"
    if not m.signature:
        return False, "mandate is not signed"
    expected_sig = _hmac_hex(mandate_payload(m), secret)
    if not hmac.compare_digest(expected_sig, m.signature):
        return False, "mandate signature does not verify (fields changed or wrong secret)"
    if m.charter_sha256 != charter_sha256:
        return False, (
            f"mandate was approved for charter {m.charter_sha256[:8]}, not the current charter "
            f"{charter_sha256[:8]}"
        )
    if not 1 <= round_n <= len(m.recipients):
        return False, f"round {round_n} is outside the mandate's {len(m.recipients)} rounds"
    expected_recipient = m.recipients[round_n - 1]
    if recipient != expected_recipient:
        return False, f"mandate names {expected_recipient} as round {round_n}'s recipient, not {recipient}"
    amt: Decimal = money(amount)
    if amt <= 0:
        return False, f"payout amount {amt} must be positive"
    if amt > m.cap_gross_per_round:
        return False, f"payout amount {amt} exceeds the mandate cap {m.cap_gross_per_round} per round"
    if today > m.expires:
        return False, f"mandate expired on {m.expires.isoformat()} (today is {today.isoformat()})"
    return True, (
        f"mandate ok: round {round_n} pays {recipient} {amt} (cap {m.cap_gross_per_round}), "
        f"valid through {m.expires.isoformat()}"
    )
