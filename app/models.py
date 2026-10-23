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
    __tablename__ = "inbox_messages"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    json: str = "{}"
    ts: datetime = Field(default_factory=utcnow)


class Remedy(SQLModel, table=True):
    __tablename__ = "remedies"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    json: str = "{}"
    ts: datetime = Field(default_factory=utcnow)


class Vote(SQLModel, table=True):
    __tablename__ = "votes"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True)
    json: str = "{}"
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
    Remedy,
    Vote,
    LlmCache,
    Metric,
)
