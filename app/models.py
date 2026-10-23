"""SQLModel tables (docs/CONTRACTS.md, B4). Table names are plural snake_case.

Money columns are strings with two decimals ("200.00"); convert with decimal.Decimal at the edges.
Datetimes are timezone-aware UTC (sqlmodel refuses naive values and returns UTC-aware ones).
"""

import warnings
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

# The contract names the signed-JSON column `json`, which shadows pydantic's deprecated `.json()` method.
warnings.filterwarnings("ignore", message='Field name "json"', category=UserWarning)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Session(SQLModel, table=True):
    """One browser session (cookie tk_sid) owning one cloned circle."""

    __tablename__ = "sessions"

    sid: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    circle_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    live_runs: int = 0


class Circle(SQLModel, table=True):
    __tablename__ = "circles"

    id: str = Field(primary_key=True, max_length=6)
    run_id: str = Field(index=True)
    name: str
    charter_json: str
    charter_sha256: str
    fee_policy: str = "net_pot"
    status: str = "active"  # active | paused | closed
    is_fixture_clone: bool = False
    virtual_now: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)
    # Judge-mode flags ({"ravi_declines": bool, "next_payout_unclaimed": bool}) and UI counters.
    flags_json: str = "{}"
    counters_json: str = "{}"
    # Effective payout order after approved turn swaps (JSON list of handles); None = the charter's rotation.
    rotation_json: str | None = None
    # Builder B (join/compile): who created a non-fixture circle and what kind it is.
    owner_sid: str | None = Field(default=None, index=True)  # session that created it (judge_mini | compiled)
    kind: str | None = None  # None/"fixture" = the demo clone | "judge_mini" | "compiled"


class MemberRow(SQLModel, table=True):
    __tablename__ = "members"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    handle: str
    name: str
    lang: str = "en"
    email: str | None = None
    payday_rule: str | None = None
    vault_id: str | None = None
    customer_id: str | None = None
    setup_token_id: str | None = None
    payout_wallet: str = "PAYPAL"  # PAYPAL | VENMO
    payout_receiver: str | None = None
    sandbox_account: str | None = None  # never exposed on the wire
    rotation_pos: int = 0
    track_record_rounds: int = 0
    # Builder B (join flow): last join result shown on the member's phone page.
    # invited | pending_approval | vaulted | cancelled | action_required | declined | error
    join_status: str | None = None
    join_message: str | None = None
    join_attempts: int | None = None  # setup tokens created (bumps the setup Request-Id suffix)
    joined_at: datetime | None = None
    # Fixer (2026-10-24): signs into the invite HMAC; rotated on VAULTED so pre-join links stop working.
    invite_nonce: str | None = None


class Round(SQLModel, table=True):
    __tablename__ = "rounds"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    n: int
    due_at: datetime
    recipient_handle: str
    status: str = "PENDING"  # PENDING | COLLECTING | COLLECTED | PAID | VOID
    payout_batch_id: str | None = None
    payout_item_id: str | None = None
    payout_status: str | None = None  # PayPal's strings verbatim (SUCCESS, UNCLAIMED, PENDING, FAILED, ...)
    payout_amount: str | None = None
    rev: int = 1
    last_capture_at: datetime | None = None
    paid_at: datetime | None = None


class Contribution(SQLModel, table=True):
    __tablename__ = "contributions"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    round_n: int
    member_handle: str
    order_id: str | None = None
    capture_id: str | None = None
    gross: str | None = None
    fee: str | None = None
    net: str | None = None
    status: str = "PENDING"  # PENDING | COMPLETED | DECLINED | COVERED
    attempt: int = 0
    decline_code: str | None = None
    decline_source: str | None = None  # api | webhook
    webhook_verified: bool = False
    predicted: bool = False
    label: str | None = None
    request_id: str | None = None
    last_attempt_at: datetime | None = None  # virtual-clock time of the last charge attempt (retry spacing)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LedgerRow(SQLModel, table=True):
    __tablename__ = "ledger_rows"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    kind: (
        str  # contribution_net | buffer_seed | buffer_advance | advance_repaid | payout | payout_fee | refund
    )
    amount: str
    round_n: int | None = None
    member_handle: str | None = None
    ref: str | None = None
    ts: datetime = Field(default_factory=utcnow)


class MandateRow(SQLModel, table=True):
    __tablename__ = "mandates"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    json: str  # signed app.domain.mandate.Mandate as JSON
    approved_at: datetime = Field(default_factory=utcnow)


class AgentTrace(SQLModel, table=True):
    __tablename__ = "agent_traces"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    run_id: str
    ts: datetime = Field(default_factory=utcnow)
    kind: str  # text | tool_call | tool_result | system
    payload: str


class WebhookEvent(SQLModel, table=True):
    __tablename__ = "webhook_events"

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(unique=True, index=True)
    event_type: str
    resource_id: str | None = None
    verified: bool = False
    received_at: datetime = Field(default_factory=utcnow)
    payload: str = ""


class PayPalCall(SQLModel, table=True):
    __tablename__ = "paypal_calls"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow)
    method: str
    path: str
    status: int | None = None
    request_id: str | None = None
    ms: int | None = None


class InboxMessage(SQLModel, table=True):
    """A member message (in) or a treasurer message to a member (out). Text is data, never instructions."""

    __tablename__ = "inbox_messages"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    json: str = "{}"
    ts: datetime = Field(default_factory=utcnow)
    member: str | None = None
    lang: str | None = None
    text: str | None = None
    direction: str | None = None  # in | out
    source: str | None = None  # judge | member | agent | rules
    round_n: int | None = None


class RemedySetRow(SQLModel, table=True):
    """One proposed remedy set (app.domain.remedies.RemedySet) and its approval state."""

    __tablename__ = "remedy_sets"

    id: str = Field(primary_key=True)  # rs_xxxxxxxx
    circle_id: str = Field(index=True)
    member: str
    round_n: int
    status: str = (
        "proposed"  # invalid | proposed | voting | approved | executed | repaid | failed | superseded
    )
    source: str = "rules"  # agent | rules
    charter_sha256: str = ""
    json: str = "{}"  # the RemedySet
    problems_json: str = "[]"
    commitment_json: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    vote_opened_at: datetime | None = None  # virtual clock
    vote_closes_at: datetime | None = None  # virtual clock (+48 h)
    approval_requested_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    executed_at: datetime | None = None
    result_json: str | None = None


class Remedy(SQLModel, table=True):
    """One remedy of a set, with its execution state (invoice id/QR for repay_by_invoice)."""

    __tablename__ = "remedies"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    json: str = "{}"  # the domain Remedy
    ts: datetime = Field(default_factory=utcnow)
    remedy_id: str | None = Field(default=None, index=True)
    set_id: str | None = Field(default=None, index=True)
    type: str | None = None
    member: str | None = None
    round_n: int | None = None
    status: str | None = None  # proposed | executed | scheduled | failed
    label: str | None = None
    invoice_id: str | None = Field(default=None, index=True)
    invoice_status: str | None = None  # SENT | PAID | FAILED | ...
    invoice_qr: str | None = None
    invoice_simulated: bool | None = None
    executed_at: datetime | None = None


class Vote(SQLModel, table=True):
    """A member vote, an affected member's consent or the organizer's approval on a remedy set."""

    __tablename__ = "votes"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    json: str = "{}"
    ts: datetime = Field(default_factory=utcnow)
    set_id: str | None = Field(default=None, index=True)
    member: str | None = None  # member handle, or "priya" (organizer) for kind == approval
    kind: str | None = None  # vote | consent | approval
    choice: str | None = None  # pending | yes | no
    source: str | None = None  # judge | member | agent


class CommitmentRow(SQLModel, table=True):
    """A member's payment commitment extracted from a reply (record_commitment)."""

    __tablename__ = "commitments"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    member: str
    amount: str
    date: str  # ISO date
    source: str = "rules"  # agent | rules
    message_id: int | None = None
    round_n: int | None = None
    ts: datetime = Field(default_factory=utcnow)


class LlmCache(SQLModel, table=True):
    __tablename__ = "llm_cache"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str | None = Field(default=None, index=True)
    key: str | None = Field(default=None, index=True)
    json: str = "{}"
    ts: datetime = Field(default_factory=utcnow)


class Metric(SQLModel, table=True):
    __tablename__ = "metrics"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str | None = Field(default=None, index=True)
    json: str = "{}"
    ts: datetime = Field(default_factory=utcnow)


ALL_TABLES = (
    Session,
    Circle,
    MemberRow,
    Round,
    Contribution,
    LedgerRow,
    MandateRow,
    AgentTrace,
    WebhookEvent,
    PayPalCall,
    InboxMessage,
    RemedySetRow,
    Remedy,
    Vote,
    CommitmentRow,
    LlmCache,
    Metric,
)


class ForecastRow(SQLModel, table=True):
    """One member's T-3 shortfall forecast for one round (app.agent.forecast, app.engine.forecast_job)."""

    __tablename__ = "forecasts"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    round_n: int
    member: str
    score: str  # "0.85" (two places)
    flagged: bool = False
    reason: str = ""
    nudge: str | None = None  # in the member's language (flagged members only)
    nudge_en: str | None = None
    lang: str = "en"
    mode: str = "rules"  # live | replay | rules (who wrote reason + nudge; the score is always rules)
    factors_json: str = "[]"
    as_of: datetime | None = None  # virtual clock when it ran
    ts: datetime = Field(default_factory=utcnow)


# --- Builder E (core + Judge Mode + D1 learnings, 2026-10-23): appended tables -----------------------


class PayoutAttempt(SQLModel, table=True):
    """One Payouts batch sent for a round (rev 1 = the original, rev 2+ = UNCLAIMED re-routes).

    Never stores the receiver itself (MemberRow holds it); `receiver_kind` says which one was used.
    `funding`: ledger (a normal pot payout) | refund (re-route after a successful cancel; a `refund` LedgerRow
    returned the first item's amount) | fronted_pending_return (cancel failed in sandbox; the business account
    fronts the re-route and the first item's funds are "pending return": no ledger refund is written until a
    cancel succeeds, so nothing is ever counted twice).
    """

    __tablename__ = "payout_attempts"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    round_n: int
    rev: int = 1
    sender_batch_id: str | None = None
    batch_id: str | None = None
    item_id: str | None = Field(default=None, index=True)
    receiver_kind: str | None = None  # payout_receiver | email | unclaimed_test
    wallet: str = "PAYPAL"
    amount: str = "0.00"
    fee: str | None = None
    status: str | None = None  # PayPal's item status verbatim
    funding: str = "ledger"  # ledger | refund | fronted_pending_return
    cancel_status: str | None = None  # None | canceled | cancel_failed
    cancel_error: str | None = None
    return_status: str | None = None  # None | pending_return | returned
    superseded_by_rev: int | None = None
    label: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class JudgeStoryRow(SQLModel, table=True):
    """Judge Mode "Next step" story progress for one circle (steps_json: list of step records)."""

    __tablename__ = "judge_stories"

    circle_id: str = Field(primary_key=True)
    sid: str = Field(index=True)
    run_id: str = ""
    steps_json: str = "[]"
    pending_step: str | None = None
    pending_task_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class ReplayRecording(SQLModel, table=True):
    """A recorded story run (events per step), served read-only at GET /api/replay when sessions are capped."""

    __tablename__ = "replay_recordings"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    run_id: str = ""
    canonical: bool = False
    paypal_mode: str = "simulated"  # live | simulated
    json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


# --- Builder B (charter compiler + join flow), appended 2026-10-23 -----------------------------------------


class CharterDraftRow(SQLModel, table=True):
    """A compiled (not yet confirmed) charter draft: POST /api/circles/compile."""

    __tablename__ = "charter_drafts"

    id: str = Field(primary_key=True)  # cd_xxxxxxxx
    sid: str = Field(index=True)  # the organizer session that compiled it
    circle_id: str | None = None  # the session's circle at compile time
    json: str = "{}"  # app.agent.charter.CompileResult
    charter_sha256: str | None = None  # None when the draft did not validate
    mode: str = "rules"  # live | replay | rules
    source: str = "parser"  # model | parser | template
    created_at: datetime = Field(default_factory=utcnow)


class CharterApprovalRow(SQLModel, table=True):
    """The organizer's confirmation of a frozen charter (consent type 1: charter and rotation)."""

    __tablename__ = "charter_approvals"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)  # the circle the approved charter governs
    charter_sha256: str
    draft_id: str | None = Field(default=None, index=True)
    approved_by: str = "priya (organizer)"
    user_agent: str = ""
    approved_at: datetime = Field(default_factory=utcnow)
