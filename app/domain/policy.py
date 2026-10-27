"""The policy engine: deterministic allow-or-deny in front of every money tool. Pure logic.

The model proposes, the policy engine decides, humans approve. ``PolicyEngine.authorize`` either returns a
``PolicyTicket`` (which the money tools require) or raises ``PolicyDenied`` with a short reason a judge can
read on screen. Rules follow PLAN.md section 5 "Guardrails and policy limits":

- frozen circle (kill switch or "Pause circle"): everything is denied
- charge / retry_charge: (only when ``CircleState.member_mandate_ok`` is not None: the member's signed mandate is
  valid, unexpired and allows the amount) exactly ``charter.amount``; one success per member per round; at most 2 retries
  per member per round, 24 virtual hours apart when the clock is known; never after PAYER_ACTION_REQUIRED;
  never for a member who revoked consent
- payout: recipient is the rotation's recipient for the round (or the approved swap); a verified mandate
  or an organizer approval; every contribution COMPLETED or COVERED; amount <= min(gross pot cap,
  circle_available); PayPal available >= amount when known; exactly one live payout per round
- buffer_advance: in the remedy menu; <= buffer balance; <= one net contribution; one open advance per
  member; only for a share that is still unpaid
- turn_swap: in the remedy menu; both members consent; at most one swap per member; only unpaid turns;
  the result is still a permutation that satisfies the collateral rule
- refund: organizer approval; at most one contribution; only a completed contribution
- cancel_unclaimed: only when a live payout exists
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain.charter import Charter, charter_sha256, collateral_violation, rounds_total, to_utc
from app.domain.fees import Money, MoneyInput, NonNegativeMoney, estimate_fee, money, net_of

Action = Literal[
    "charge", "payout", "buffer_advance", "turn_swap", "refund", "cancel_unclaimed", "retry_charge"
]
ContributionStatus = Literal["PENDING", "COMPLETED", "DECLINED", "COVERED"]

ACTIONS: tuple[str, ...] = get_args(Action)
ORGANIZER = "organizer"  # the consent token for the organizer's one-tap approval
MAX_RETRIES = 2  # per member per round, on top of the first attempt
RETRY_SPACING = timedelta(hours=24)  # virtual hours between attempts
NEVER_RETRY_CODES = frozenset({"PAYER_ACTION_REQUIRED"})


class CircleState(BaseModel):
    """Snapshot of one circle at one round, built by the engine from the DB and the ledger."""

    circle_id: str
    run_id: str
    round_n: int = Field(ge=1)
    charter_sha256: str
    frozen: bool = False
    contributions: dict[str, ContributionStatus]  # member handle -> status for round_n
    attempts: dict[str, int] = Field(default_factory=dict)  # member -> charge attempts this round
    buffer_balance: NonNegativeMoney
    circle_available: Money
    paypal_available: Money | None = None  # account-wide balance; None when the Balances call failed
    open_advances: dict[str, Money] = Field(default_factory=dict)  # member -> open advance
    swaps_done: set[str] = Field(default_factory=set)  # members who already swapped once
    live_payout_exists: bool = False
    mandate_ok: bool = False  # verify_mandate passed for this round and amount
    # Optional extras the engine fills in when it knows them (all additive, all default off):
    rotation_override: list[str] | None = None  # effective payout order after approved swaps
    last_attempt_at: dict[str, datetime] = Field(default_factory=dict)  # member -> last charge attempt
    decline_codes: dict[str, str] = Field(default_factory=dict)  # member -> last decline issue code
    revoked: set[str] = Field(default_factory=set)  # members whose vault token was deleted
    now: datetime | None = None  # the circle's virtual clock
    # Member mandates (flag ``member_mandates``). None = NOT required: the policy path is exactly the old one.
    # A dict = required: {member: {"ok": bool, "reason": str, "max_charge_per_round": "200.00" | None}}, built by
    # ``app.domain.member_mandate.policy_entry``. A charge for a member with no entry, an invalid or expired
    # mandate, or an amount over the limit is denied before any PayPal call.
    member_mandate_ok: dict[str, dict[str, Any]] | None = None


class PolicyTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # uuid4 hex
    action: Action
    circle_id: str
    round_n: int
    member: str | None = None
    amount: Money | None = None
    issued_at: datetime  # UTC


class PolicyDenied(Exception):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PolicyEngine:
    def __init__(self, charter: Charter, state: CircleState) -> None:
        self.charter = charter
        self.state = state
        self.charter_hash = charter_sha256(charter)
        self.handles: list[str] = [m.handle for m in charter.members]

    # --- derived limits -----------------------------------------------------------------------------

    @property
    def gross_pot_cap(self) -> Decimal:
        """The most a round can ever pay: every member's gross contribution."""
        return money(self.charter.amount * rounds_total(self.charter))

    @property
    def one_net_contribution(self) -> Decimal:
        """What one contribution brings in after PayPal's fee; the ceiling for a buffer advance."""
        return net_of(self.charter.amount, estimate_fee(self.charter.amount))

    def effective_rotation(self) -> list[str]:
        return list(self.state.rotation_override or self.charter.rotation)

    def expected_recipient(self) -> str:
        return self.effective_rotation()[self.state.round_n - 1]

    # --- entry point --------------------------------------------------------------------------------

    def authorize(
        self,
        action: Action,
        *,
        member: str | None = None,
        amount: MoneyInput | None = None,
        recipient: str | None = None,
        swap_with: str | None = None,
        consents: Collection[str] = frozenset(),
    ) -> PolicyTicket:
        """Return a ticket for ``action`` or raise ``PolicyDenied`` with the first rule that fails."""
        s = self.state
        if s.frozen:
            raise PolicyDenied("circle is frozen: every money action is blocked until it is resumed")
        if s.charter_sha256 != self.charter_hash:
            raise PolicyDenied(
                f"charter hash mismatch: state is for {s.charter_sha256[:8]}, charter is {self.charter_hash[:8]}"
            )
        if action not in ACTIONS:
            raise PolicyDenied(f"unknown action {action!r}")
        if not 1 <= s.round_n <= rounds_total(self.charter):
            raise PolicyDenied(f"round {s.round_n} is out of range 1..{rounds_total(self.charter)}")
        consents = set(consents)
        if action in ("charge", "retry_charge"):
            member, amt = self._charge(member, amount, retry=action == "retry_charge")
        elif action == "payout":
            member, amt = self._payout(recipient, amount, consents)
        elif action == "buffer_advance":
            member, amt = self._buffer_advance(member, amount)
        elif action == "turn_swap":
            member, amt = self._turn_swap(member, swap_with, consents), None
        elif action == "refund":
            member, amt = self._refund(member, amount, consents)
        else:  # cancel_unclaimed
            member, amt = self._cancel_unclaimed(recipient), None
        return PolicyTicket(
            id=uuid4().hex,
            action=action,
            circle_id=s.circle_id,
            round_n=s.round_n,
            member=member,
            amount=amt,
            issued_at=datetime.now(UTC),
        )

    # --- helpers ------------------------------------------------------------------------------------

    def _member(self, handle: str | None, label: str = "member") -> str:
        if not handle:
            raise PolicyDenied(f"{label} is required")
        if handle not in self.handles:
            raise PolicyDenied(f"unknown {label} {handle!r}")
        return handle

    def _amount(self, amount: MoneyInput | None, label: str) -> Decimal:
        if amount is None:
            raise PolicyDenied(f"{label} amount is required")
        try:
            amt = money(amount)
        except ValueError as e:
            raise PolicyDenied(f"{label} amount is not a valid money value: {amount!r}") from e
        if amt <= 0:
            raise PolicyDenied(f"{label} amount {amt} must be positive")
        return amt

    def _remedy_allowed(self, remedy: str) -> None:
        if remedy not in self.charter.remedy_menu:
            menu = ", ".join(self.charter.remedy_menu) or "empty"
            raise PolicyDenied(f"{remedy} is not in the charter's remedy menu ({menu})")

    def _status(self, member: str) -> str:
        return self.state.contributions.get(member, "PENDING")

    # --- rules --------------------------------------------------------------------------------------

    def _charge(self, member: str | None, amount: MoneyInput | None, *, retry: bool) -> tuple[str, Decimal]:
        s, c = self.state, self.charter
        m = self._member(member)
        amt = c.amount if amount is None else self._amount(amount, "charge")
        self._member_mandate_allows(m, amt)
        if amt != c.amount:
            raise PolicyDenied(f"charge amount {amt} differs from the charter amount {c.amount}")
        if m in s.revoked:
            raise PolicyDenied(f"{m} revoked consent (vault token deleted); charges are blocked")
        status = self._status(m)
        if status == "COMPLETED":
            raise PolicyDenied(f"{m} already has a completed charge for round {s.round_n}")
        if status == "COVERED":
            raise PolicyDenied(f"{m}'s share for round {s.round_n} is already covered")
        prior = s.attempts.get(m, 0)
        if retry and (prior == 0 or status != "DECLINED"):
            raise PolicyDenied(f"nothing to retry: {m} is {status} for round {s.round_n}")
        retries_used = max(prior - 1, 0)
        if retries_used >= MAX_RETRIES:
            raise PolicyDenied(f"{m} has used {retries_used} of {MAX_RETRIES} retries for round {s.round_n}")
        if prior >= 1:  # this attempt is a retry, whatever the caller named it
            code = s.decline_codes.get(m)
            if code in NEVER_RETRY_CODES:
                raise PolicyDenied(f"{m}'s last decline was {code}; it is never retried")
            last = s.last_attempt_at.get(m)
            if last is not None and s.now is not None:
                elapsed = to_utc(s.now) - to_utc(last)
                if elapsed < RETRY_SPACING:
                    hours = elapsed.total_seconds() / 3600
                    raise PolicyDenied(
                        f"retry for {m} is too soon: {hours:.0f}h since the last attempt, 24h required"
                    )
        return m, amt

    def _member_mandate_allows(self, member: str, amt: Decimal) -> None:
        """Deny a charge outside the member's mandate. A no-op when member mandates are not required (None)."""
        required = self.state.member_mandate_ok
        if required is None:
            return
        entry = required.get(member)
        if not entry:
            raise PolicyDenied(f"no member mandate on file for {member}; charges are blocked")
        if not entry.get("ok"):
            raise PolicyDenied(f"{entry.get('reason') or f'{member} has no valid member mandate'}; charge blocked")
        limit = entry.get("max_charge_per_round")
        if limit is None:
            raise PolicyDenied(f"{member}'s mandate has no charge limit; charge blocked")
        if amt > money(limit):
            raise PolicyDenied(f"charge {amt} exceeds {member}'s mandate limit {money(limit)} per round")

    def _payout(
        self, recipient: str | None, amount: MoneyInput | None, consents: set[str]
    ) -> tuple[str, Decimal]:
        s = self.state
        amt = self._amount(amount, "payout")
        expected = self.expected_recipient()
        rcpt = recipient or expected
        if rcpt != expected:
            swapped = s.rotation_override is None and rcpt in s.swaps_done and expected in s.swaps_done
            if not swapped:
                raise PolicyDenied(f"payout recipient {rcpt} is not round {s.round_n}'s recipient {expected}")
        if rcpt not in self.handles:
            raise PolicyDenied(f"unknown payout recipient {rcpt!r}")
        if not (s.mandate_ok or ORGANIZER in consents):
            raise PolicyDenied("payout needs a verified organizer mandate or an organizer approval")
        unpaid = [h for h in self.handles if self._status(h) not in ("COMPLETED", "COVERED")]
        if unpaid:
            detail = ", ".join(f"{h} is {self._status(h)}" for h in unpaid)
            raise PolicyDenied(f"round {s.round_n} is not fully collected: {detail}")
        cap = self.gross_pot_cap
        if amt > s.circle_available and s.circle_available <= cap:
            raise PolicyDenied(f"payout amount {amt} exceeds circle available {s.circle_available}")
        if amt > cap:
            raise PolicyDenied(f"payout amount {amt} exceeds the gross pot cap {cap}")
        if s.paypal_available is not None and amt > s.paypal_available:
            raise PolicyDenied(
                f"payout amount {amt} exceeds the PayPal available balance {s.paypal_available}"
            )
        if s.live_payout_exists:
            raise PolicyDenied(f"a live payout already exists for round {s.round_n}; exactly one per round")
        return rcpt, amt

    def _buffer_advance(self, member: str | None, amount: MoneyInput | None) -> tuple[str, Decimal]:
        s = self.state
        m = self._member(member)
        self._remedy_allowed("buffer_advance")
        amt = self._amount(amount, "buffer advance")
        status = self._status(m)
        if status == "COMPLETED":
            raise PolicyDenied(f"{m} already paid round {s.round_n}; there is nothing to cover")
        if status == "COVERED":
            raise PolicyDenied(f"{m}'s share for round {s.round_n} is already covered")
        open_advance = s.open_advances.get(m)
        if open_advance:
            raise PolicyDenied(f"{m} already has an open advance of {open_advance}; at most one per member")
        if amt > s.buffer_balance:
            raise PolicyDenied(f"buffer advance {amt} exceeds the buffer balance {s.buffer_balance}")
        if amt > self.one_net_contribution:
            raise PolicyDenied(
                f"buffer advance {amt} exceeds one net contribution {self.one_net_contribution}"
            )
        return m, amt

    def _turn_swap(self, member: str | None, swap_with: str | None, consents: set[str]) -> str:
        s = self.state
        m = self._member(member)
        w = self._member(swap_with, "swap_with")
        if m == w:
            raise PolicyDenied(f"{m} cannot swap turns with themselves")
        self._remedy_allowed("turn_swap")
        missing = [h for h in (m, w) if h not in consents]
        if missing:
            raise PolicyDenied(
                f"turn swap needs consent from both {m} and {w}; missing: {', '.join(missing)}"
            )
        already = [h for h in (m, w) if h in s.swaps_done]
        if already:
            raise PolicyDenied(f"{', '.join(already)} already swapped once; at most one swap per member")
        rotation = self.effective_rotation()
        i, j = rotation.index(m), rotation.index(w)
        for handle, pos in ((m, i), (w, j)):
            if pos + 1 < s.round_n:
                raise PolicyDenied(f"{handle}'s turn (round {pos + 1}) is already paid and cannot be swapped")
            if pos + 1 == s.round_n and s.live_payout_exists:
                raise PolicyDenied(
                    f"{handle}'s turn (round {pos + 1}) has a live payout and cannot be swapped"
                )
        rotation[i], rotation[j] = rotation[j], rotation[i]
        violation = collateral_violation(rotation, self.charter.members)
        if violation is not None:
            untracked, tracked = violation
            raise PolicyDenied(
                f"swap would put {untracked} (no track record) before {tracked} (has a track record); "
                "the collateral rule forbids it"
            )
        return m

    def _refund(
        self, member: str | None, amount: MoneyInput | None, consents: set[str]
    ) -> tuple[str, Decimal]:
        s, c = self.state, self.charter
        m = self._member(member)
        amt = self._amount(amount, "refund")
        if amt > c.amount:
            raise PolicyDenied(f"refund {amt} exceeds one contribution {c.amount}")
        if self._status(m) != "COMPLETED":
            raise PolicyDenied(f"no completed contribution to refund for {m} in round {s.round_n}")
        if ORGANIZER not in consents:
            raise PolicyDenied(f"refund to {m} needs the organizer's approval")
        return m, amt

    def _cancel_unclaimed(self, recipient: str | None) -> str:
        s = self.state
        if not s.live_payout_exists:
            raise PolicyDenied(f"no live payout to cancel for round {s.round_n}")
        return recipient or self.expected_recipient()
