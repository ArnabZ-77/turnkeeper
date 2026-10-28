"""Member mandates: what each member's agent may do on their behalf. Pure logic, no I/O, no settings.

HONEST LABELS (shown wherever a mandate is shown):

- ``MANDATE_LABEL``: an AP2-style Intent Mandate, our own implementation. PayPal's AP2 APIs are not public and
  Turnkeeper does not claim AP2 support.
- ``SIGNATURE_LABEL``: a server-attested HMAC-SHA256 signature, not a key held by the member.

A member mandate is *external* to the charter (the charter hash never changes). It binds one member in one
circle to one charter hash and says: the treasurer may charge at most ``max_charge_per_round`` per round until
``expires``, and the member's agent may answer remedy consents and votes inside the three remedy rules
(``accept_earlier_swap``, ``max_advance``, ``require_ask_for_later_swap``). The server signs the canonical JSON of
every field except ``signature`` with the secret the caller passes in, reusing the canonical encoding
(``canonical_dumps``) and the HMAC helper of ``app.domain.mandate``.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.charter import Charter, canonical_dumps, to_utc
from app.domain.fees import MoneyInput, NonNegativeMoney, PositiveMoney, money
from app.domain.mandate import hmac_hex

MANDATE_LABEL = "AP2-style Intent Mandate, our own implementation; PayPal AP2 APIs are not public, we do not claim AP2 support"
SIGNATURE_LABEL = "server-attested HMAC signature, not a key held by the member"
SCHEME = "turnkeeper-member-intent-mandate-v1"
ATTESTATION: Literal["server-hmac-sha256"] = "server-hmac-sha256"

# The demo member whose rule the on-screen story names: accepts earlier turns, asks for later ones.
TOMAS_RULE = "accepts earlier turns, asks for later ones"


class MemberMandate(BaseModel):
    model_config = ConfigDict(frozen=True)

    scheme: str = SCHEME
    attestation: str = ATTESTATION
    member: str = Field(min_length=1)  # charter handle
    circle_id: str = Field(min_length=1)
    charter_sha256: str = Field(min_length=1)
    max_charge_per_round: PositiveMoney
    expires: date
    approved_at: datetime  # stored as UTC
    approved_by: str = Field(min_length=1)
    # --- the member's agent rules (what it may answer without asking the human) ---
    accept_earlier_swap: bool = True  # a swap that moves this member's payout earlier: the agent says yes
    max_advance: NonNegativeMoney  # the largest buffer advance the agent will vote yes on (0 = never)
    require_ask_for_later_swap: bool = True  # a swap that moves this member later: the agent asks the human
    signature: str = ""  # hex HMAC-SHA256, set by sign_member_mandate

    @field_validator("approved_at")
    @classmethod
    def _approved_at_utc(cls, v: datetime) -> datetime:
        return to_utc(v)

    @property
    def rule_summary(self) -> str:
        earlier = "accepts earlier turns" if self.accept_earlier_swap else "asks before an earlier turn"
        later = "asks for later ones" if self.require_ask_for_later_swap else "accepts later ones"
        return f"{earlier}, {later}"


def member_mandate_payload(m: MemberMandate) -> str:
    """The signed bytes: canonical JSON of every field except ``signature``."""
    return canonical_dumps(m.model_dump(mode="python", exclude={"signature"}))


def sign_member_mandate(m: MemberMandate, secret: str) -> MemberMandate:
    """A copy of ``m`` with ``signature`` = hex HMAC-SHA256(secret, canonical non-signature fields)."""
    if not secret:
        raise ValueError("mandate secret must not be empty")
    return m.model_copy(update={"signature": hmac_hex(member_mandate_payload(m), secret)})


def verify_member_mandate(
    m: MemberMandate,
    secret: str,
    *,
    circle_id: str,
    member: str,
    charter_sha256: str,
    today: date,
    amount: MoneyInput | None = None,
) -> tuple[bool, str]:
    """``(ok, reason)``: signature, circle, member, charter hash, not expired, and ``amount`` (when given) within
    ``max_charge_per_round``. The first failing check names the reason."""
    if not secret:
        return False, "mandate secret is not configured"
    if not m.signature:
        return False, f"{member}'s mandate is not signed"
    expected = hmac_hex(member_mandate_payload(m), secret)
    if not hmac.compare_digest(expected, m.signature):
        return False, f"{member}'s mandate signature does not verify (fields changed or wrong secret)"
    if m.circle_id != circle_id:
        return False, f"mandate belongs to circle {m.circle_id}, not {circle_id}"
    if m.member != member:
        return False, f"mandate was approved for {m.member}, not {member}"
    if m.charter_sha256 != charter_sha256:
        return False, (
            f"{member}'s mandate was approved for charter {m.charter_sha256[:8]}, not the current charter "
            f"{charter_sha256[:8]}"
        )
    if today > m.expires:
        return False, f"{member}'s mandate expired on {m.expires.isoformat()} (today is {today.isoformat()})"
    if amount is not None:
        amt: Decimal = money(amount)
        if amt <= 0:
            return False, f"amount {amt} must be positive"
        if amt > m.max_charge_per_round:
            return False, f"amount {amt} exceeds {member}'s mandate limit {m.max_charge_per_round} per round"
    return True, (
        f"mandate ok: {member} up to {m.max_charge_per_round} per round, valid through {m.expires.isoformat()}"
    )


def policy_entry(
    m: MemberMandate | None,
    secret: str,
    *,
    circle_id: str,
    member: str,
    charter_sha256: str,
    today: date,
) -> dict[str, Any]:
    """The per-member record ``CircleState.member_mandate_ok`` holds: ``{"ok", "reason", "max_charge_per_round"}``.

    ``max_charge_per_round`` is None when there is no mandate (the policy then denies with ``reason``)."""
    if m is None:
        return {
            "ok": False,
            "reason": f"no member mandate on file for {member}",
            "max_charge_per_round": None,
        }
    ok, reason = verify_member_mandate(
        m, secret, circle_id=circle_id, member=member, charter_sha256=charter_sha256, today=today
    )
    return {"ok": ok, "reason": reason, "max_charge_per_round": str(m.max_charge_per_round)}


# --- defaults for the demo members ----------------------------------------------------------------------


def default_member_mandate(
    charter: Charter,
    member: str,
    *,
    circle_id: str,
    charter_sha256: str,
    approved_at: datetime,
    expires: date,
) -> MemberMandate:
    """The unsigned default mandate: one contribution per round, advances up to one contribution, accepts earlier
    swaps and asks for later ones. (Tomas's rule is the default; it is the documented demo rule.)"""
    if member not in {m.handle for m in charter.members}:
        raise ValueError(f"unknown member {member!r}")
    return MemberMandate(
        member=member,
        circle_id=circle_id,
        charter_sha256=charter_sha256,
        max_charge_per_round=charter.amount,
        expires=expires,
        approved_at=approved_at,
        approved_by=f"server-attested default for {member} (demo)",
        accept_earlier_swap=True,
        max_advance=charter.amount,
        require_ask_for_later_swap=True,
    )


def default_member_mandates(
    charter: Charter,
    *,
    circle_id: str,
    charter_sha256: str,
    approved_at: datetime,
    expires: date,
    secret: str,
    members: Iterable[str] | None = None,
) -> list[MemberMandate]:
    """Signed default mandates for ``members`` (every charter member by default), in charter order."""
    wanted = set(members) if members is not None else None
    return [
        sign_member_mandate(
            default_member_mandate(
                charter,
                m.handle,
                circle_id=circle_id,
                charter_sha256=charter_sha256,
                approved_at=approved_at,
                expires=expires,
            ),
            secret,
        )
        for m in charter.members
        if wanted is None or m.handle in wanted
    ]
