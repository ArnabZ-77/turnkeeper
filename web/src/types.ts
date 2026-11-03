/**
 * Wire types for the Turnkeeper UI.
 *
 * These mirror docs/CONTRACTS.md: the BoardModel served by `GET /api/circles/{id}` and the SSE
 * payloads from `GET /api/events`. Money is always a `"200.00"` string on the wire;
 * the UI formats it and never does arithmetic on it.
 */

export type ContributionStatus = "PENDING" | "COMPLETED" | "DECLINED" | "COVERED";
export type RoundStatus = "PENDING" | "COLLECTING" | "COLLECTED" | "PAID" | "VOID";
export type Wallet = "PAYPAL" | "VENMO";
export type Cadence = "monthly" | "weekly";

export interface CircleFlags {
  ravi_declines: boolean;
  next_payout_unclaimed: boolean;
  /** Member mandates: present (true) only when the feature is on for this circle; absent when off. */
  member_mandates?: boolean;
  /** Present (true) only for a circle started as Story B (member agents answer steps 6-7). */
  member_agents_story?: boolean;
}

export interface CircleInfo {
  id: string;
  name: string;
  amount: string;
  currency: string;
  cadence: Cadence;
  charter_sha256: string;
  fee_policy: string;
  buffer_balance: string;
  circle_available: string;
  status: string;
  virtual_now: string | null;
  run_id: string;
  rotation: string[];
  mandate_present: boolean;
  flags: CircleFlags;
  /** Builder E (D1): the last PayPal Balances reading. Context only; the circle ledger is the binding gate. */
  paypal_balance?: PayPalBalanceInfo | null;
}

/** `circle.paypal_balance`: account-wide, lags in the sandbox; all fields null before any balance check. */
export interface PayPalBalanceInfo {
  value: string | null;
  last_refreshed: string | null;
  source?: string;
  stale: boolean | null;
  checked_at: string | null;
}

export interface MemberInfo {
  handle: string;
  name: string;
  lang: string;
  payout_wallet: Wallet;
  vaulted: boolean;
  rotation_pos: number;
  /** B6: "Round 3: covered by buffer · Invoice due Fri", "Turn moved to round 5 (swap with ravi)", ... */
  label?: string | null;
}

export interface PayoutInfo {
  batch_id: string | null;
  item_id: string | null;
  status: string;
  amount: string;
}

export interface RoundInfo {
  n: number;
  due_at: string;
  recipient: string;
  status: RoundStatus;
  payout: PayoutInfo | null;
  paid_at: string | null;
  /** Builder E (D1 UNCLAIMED path): current payout revision (1 = first batch, 2+ = re-routed). */
  payout_rev?: number | null;
  /** Every batch sent for the round (no receivers). */
  payout_attempts?: PayoutAttemptInfo[];
  /** Funds of a superseded UNCLAIMED item whose cancel failed; null when nothing is outstanding. */
  pending_return?: PendingReturnInfo | null;
}

export interface PayoutAttemptInfo {
  rev: number;
  batch_id: string | null;
  item_id: string | null;
  status: string | null;
  amount: string | null;
  wallet: string | null;
  receiver_kind: string | null;
  /** ledger | refund | fronted_pending_return */
  funding: string | null;
  /** canceled | cancel_failed | null */
  cancel_status: string | null;
  /** pending_return | returned | null */
  return_status: string | null;
  label: string | null;
}

export interface PendingReturnInfo {
  amount: string;
  items: { rev: number; item_id: string | null; amount: string | null }[];
  label: string;
}

/** Builder C (M3): one T-3 forecast row (`BoardModel.forecasts[]`). */
export interface ForecastInfo {
  round_n: number;
  member: string;
  score: number;
  flagged: boolean;
  reason: string | null;
  nudge: string | null;
  nudge_en: string | null;
  lang: string | null;
  /** live | replay | rules (rules = template wording) */
  mode: string | null;
  factors?: { factor: string; weight: number; detail: string }[];
  as_of?: string | null;
}

export interface ContributionInfo {
  round_n: number;
  member: string;
  status: ContributionStatus;
  order_id: string | null;
  capture_id: string | null;
  gross: string | null;
  fee: string | null;
  net: string | null;
  decline_code: string | null;
  decline_source: "api" | "webhook" | null;
  attempt: number;
  webhook_verified: boolean;
  predicted: boolean;
  label: string | null;
}

export interface CountersInfo {
  organizer_messages_typed: number;
  rounds_paid_on_time: number;
  shortfalls_covered: number;
}

export interface TickerInfo {
  last_capture_at: string | null;
  paid_at: string | null;
  seconds: number | null;
}

export interface BoardModel {
  circle: CircleInfo;
  members: MemberInfo[];
  rounds: RoundInfo[];
  contributions: ContributionInfo[];
  counters: CountersInfo;
  ticker: TickerInfo;
  /** B6 recovery block (absent on older servers). */
  recovery?: RecoveryInfo | null;
  /** M3 forecasts (latest round first, flagged first). */
  forecasts?: ForecastInfo[];
}

export interface SessionInfo {
  run_id: string;
  circle_id: string;
  live_ai: boolean;
  paypal_live: boolean;
  frozen: boolean;
  /** Every member of the circle has a real vault id (false -> "members not vaulted" banner). */
  vault_seeded?: boolean;
}

export interface TaskStarted {
  task_id: string;
}

/** How a treasurer run actually executed (per trace line and per task). */
export type RunMode = "live" | "replay" | "rules" | "engine";

/** `GET /api/tasks/{task_id}`. */
export interface TaskStatus {
  task_id: string;
  kind: string;
  done: boolean;
  ok: boolean | null;
  mode: RunMode | string | null;
  summary: string | null;
  error: string | null;
}

// ---------------------------------------------------------------------------------------------
// B6 recovery path (docs/CONTRACTS.md "B6 recovery path")
// ---------------------------------------------------------------------------------------------

export type RemedyType = "retry_on_date" | "turn_swap" | "buffer_advance" | "repay_by_invoice";

export interface RemedyInfo {
  id: string;
  type: RemedyType | string;
  member: string;
  round_n: number;
  params: { swap_with: string | null; amount: string | null; due_date: string | null };
  reason: string;
  status: string;
  label: string | null;
}

export interface VotesInfo {
  yes: number;
  no: number;
  needed: number;
  total: number;
  passed: boolean;
  closes_at: string | null;
  rejected?: boolean;
  /** `source` (agent | member | judge) is sent with the member-agents flag on, for answered rows only. */
  voters?: { member: string; choice: string; source?: string }[];
}

export interface ConsentsInfo {
  needed: string[];
  given: string[];
  refused: string[];
  /** Who recorded each answered consent (agent | member | judge); member-agents flag only. */
  sources?: Record<string, string>;
}

export interface ApprovalInfo {
  by: string;
  status: "pending" | "requested" | "approved" | string;
  at: string | null;
}

export interface InvoiceInfo {
  id: string;
  status: string;
  member: string;
  round_n: number;
  amount: string | null;
  due_date: string | null;
  simulated: boolean;
  /** Same-origin PNG route, or null when the invoice has no QR code. */
  qr: string | null;
  /** Only on `GET /recovery`: the QR as a data URL. */
  qr_data?: string | null;
}

export interface CommitmentInfo {
  member: string;
  amount: string;
  date: string;
  source: string;
}

export interface InboxMessage {
  id: number;
  member: string;
  lang: string;
  text: string;
  direction: "in" | "out" | string;
  source: string;
  round_n: number | null;
  ts: string | null;
}

/** One link of the chain of authority behind a charge (app/engine/passbook.py _receipt). */
export interface ReceiptLink {
  step: string;
  label: string;
  value: string | null;
}

/** GET /api/passbook (app/engine/passbook.py view): one member's own view, from stored state only. */
export interface PassbookView {
  ok: boolean;
  label: string;
  member: { handle: string; name: string; lang: string };
  circle: { name: string; currency: string; amount: string; cadence: string; rounds: number; charter_sha256: string };
  payments: {
    round_n: number;
    status: string;
    gross: string | null;
    fee: string | null;
    net: string | null;
    capture_id: string | null;
    label: string | null;
    /** No PayPal call made this row (fixture replay or simulated round); the UI says so. */
    simulated: boolean;
    receipt: ReceiptLink[];
  }[];
  this_round: { round_n: number; paid: number; total: number; covered_by_buffer: number; mine: string | null } | null;
  turn: {
    original_round: number | null;
    round_n: number | null;
    moved: boolean;
    payout_status: string | null;
    payout_amount: string | null;
    payout_batch_id: string | null;
    due: string | null;
    expected_pot: string;
  };
  fees: { paid: string; per_payment: string; policy: string };
  if_someone_misses: { buffer: string; remedy_menu: string[]; quorum: string; grace_days: number; charter_sha256: string };
  repayment: { invoice_id: string | null; status: string | null; amount: string | null; due_date: string | null; qr: boolean; simulated: boolean } | null;
  expires_at: number;
}

export interface PassbookLink {
  member: string;
  name: string;
  path: string;
}

/** GET /api/circles/{id}/recovery/injection-check (app/engine/recovery.py injection_check). */
export interface InjectionCheckView {
  ok: boolean;
  member?: string;
  planted: { text: string; amount: string; used: boolean; policy_if_used: string[] | null }[];
  proposal?: { invoice: string | null; advance: string | null };
  contribution?: string;
  charter_sha256?: string;
}

export interface RecoveryInfo {
  status: string;
  set_id: string | null;
  member: string | null;
  round_n: number | null;
  source?: string | null;
  commitment: CommitmentInfo | null;
  remedies: RemedyInfo[];
  problems: string[];
  votes: VotesInfo | null;
  consents: ConsentsInfo | null;
  approval: ApprovalInfo | null;
  blockers?: string[];
  invoice: InvoiceInfo | null;
  /** Only on `GET /recovery`: the last 20 inbox messages (oldest first). */
  inbox?: InboxMessage[];
}

export interface JudgeResult {
  ok?: boolean;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------------------------
// SSE events (`type` and `ts` are always present; see app/engine/events.py in the contract)
// ---------------------------------------------------------------------------------------------

export type TraceKind = "text" | "tool_call" | "tool_result" | "system";
export type ToastLevel = "info" | "success" | "warn" | "error";

interface EventBase {
  ts: string;
}

export interface ChipEvent extends EventBase {
  type: "chip";
  round_n: number;
  member: string;
  status: ContributionStatus;
  order_id?: string | null;
  capture_id?: string | null;
  label?: string | null;
  decline_code?: string | null;
  webhook_verified?: boolean;
  predicted?: boolean;
}

export interface TraceEvent extends EventBase {
  type: "trace";
  kind: TraceKind;
  text: string;
  tool?: string;
  args?: unknown;
  ms?: number;
  /** How this line was produced: live model, replayed recording, rules mode, or the engine directly. */
  mode?: RunMode | string;
  source?: string;
  /** The model behind an AI line (live or replay). */
  model?: string;
  /** Replay only: the UTC date (YYYY-MM-DD) the replayed turn was recorded. */
  recorded?: string;
  /** tool_call only: the model's own words before the call, one line of plain text. */
  reason?: string;
}

export interface PayoutEvent extends EventBase {
  type: "payout";
  round_n: number;
  status: string;
  batch_id?: string | null;
  item_id?: string | null;
  amount: string;
  recipient: string;
  wallet: Wallet;
  /** Re-route (D1): the new revision, the cancel outcome and any funds still pending return. */
  rev?: number;
  cancel_status?: string | null;
  pending_return?: { amount: string; item_id: string | null; label: string } | null;
}

export interface TickerEvent extends EventBase {
  type: "ticker";
  last_capture_at: string | null;
  paid_at: string | null;
  seconds: number | null;
}

export interface ToastEvent extends EventBase {
  type: "toast";
  level: ToastLevel;
  text: string;
}

export interface QueueEvent extends EventBase {
  type: "queue";
  position: number;
}

export interface BoardRefreshEvent extends EventBase {
  type: "board";
}

export interface ScheduleChangedEvent extends EventBase {
  type: "schedule_changed";
  rotation: string[];
}

export interface ForecastEvent extends EventBase {
  type: "forecast";
  round_n: number;
  member: string;
  score: number;
  reason: string | null;
  nudge: string | null;
  nudge_en: string | null;
  lang: string | null;
  mode: string | null;
}

/** Judge Mode story marker: a step started (`phase: "start"`). */
export interface StoryEvent extends EventBase {
  type: "story";
  phase: string;
  step: string;
  index?: number;
  label?: string;
}

/** Anything the server sends that this build does not know (heartbeats, future event types). */
export interface UnknownEvent extends EventBase {
  type: "unknown";
  name: string;
  raw: unknown;
}

export type TurnkeeperEvent =
  | ChipEvent
  | TraceEvent
  | PayoutEvent
  | TickerEvent
  | ToastEvent
  | QueueEvent
  | BoardRefreshEvent
  | ScheduleChangedEvent
  | ForecastEvent
  | StoryEvent
  | UnknownEvent;

export const KNOWN_EVENT_TYPES = [
  "chip",
  "trace",
  "payout",
  "ticker",
  "toast",
  "queue",
  "board",
  "schedule_changed",
  "forecast",
  "story",
] as const;

// ---------------------------------------------------------------------------------------------
// Judge Mode story (docs/CONTRACTS.md "Builder E")
// ---------------------------------------------------------------------------------------------

export type StoryStepStatus = "done" | "running" | "next" | "todo";

export interface StoryStep {
  step: string;
  label: string;
  status: StoryStepStatus | string;
  at: string | null;
  /** Legacy single mode: the AI mode for agent steps, the PayPal mode for others (story.ts storyHow splits it). */
  mode: string | null;
  summary: string | null;
  /** Newer servers: how PayPal was used (live | simulated | none). */
  paypal?: string | null;
  /** Newer servers: how the AI ran (live | replay | rules | none). */
  ai?: string | null;
  /** Newer servers: why the last attempt at this step failed. */
  error?: string | null;
}

export interface StoryView {
  ok?: boolean;
  circle_id?: string;
  run_id?: string | null;
  steps: StoryStep[];
  current: string | null;
  next: { step: string; label: string; action_label: string; index: number } | null;
  running: { step: string; task_id: string } | null;
  complete: boolean;
  paypal: "live" | "simulated" | string;
  replay_url: string | null;
  /** Only present for the opt-in Story B ("member_agents"); absent otherwise. */
  variant?: string;
}

/** `POST /api/judge/story/{step}`. */
export interface StoryStepResult {
  ok: boolean;
  step: string;
  mode?: string;
  summary?: string;
  task_id?: string | null;
  session?: SessionInfo;
  /** The step's own payload; Story B consent/vote steps carry `decisions[]` (member agents). */
  result?: Record<string, unknown> | null;
  story: StoryView;
}

// ---------------------------------------------------------------------------------------------
// Charter compiler and join flow (docs/CONTRACTS.md "Builder B")
// ---------------------------------------------------------------------------------------------

export interface CharterMemberJson {
  handle: string;
  name: string;
  lang: string;
  email?: string | null;
  payday_rule: string | null;
  payout_wallet: Wallet;
  payout_receiver?: string | null;
  track_record_rounds: number;
}

export interface CharterJson {
  name: string;
  currency: string;
  amount: string;
  cadence: Cadence;
  start_date: string;
  members: CharterMemberJson[];
  rotation: string[];
  fee_policy: string;
  buffer: { seed: string; source?: string; retention_pct?: number };
  grace_days: number;
  quorum: string;
  remedy_menu: string[];
  languages: string[];
}

export interface CompileResult {
  ok: boolean;
  mode: "live" | "replay" | "rules" | string;
  source: "model" | "parser" | "template" | string;
  mode_label: string;
  lang: string;
  charter: CharterJson | null;
  charter_sha256: string | null;
  explanations: Record<string, string>;
  problems: string[];
  notes: string[];
  draft_id: string;
  matches_circle: boolean;
  live_ai_allowed?: boolean;
}

export interface FeesInfo {
  fee_policy: string;
  fee_estimate: string;
  net_per_contribution: string;
  gross_pot: string;
  pot_estimate: string;
  payout_fee_reserve: string;
  line: string;
}

export interface BillingCycle {
  tenure_type: string;
  sequence: number;
  total_cycles: number;
  pricing_scheme: { pricing_model?: string; price: { value: string; currency_code: string } };
  frequency: { interval_unit: string; interval_count: number };
  start_date?: string;
}

export interface BillingPlan {
  name?: string;
  billing_cycles: BillingCycle[];
  one_time_charges?: { total_amount?: { value: string; currency_code: string } };
}

export interface CharterSummary {
  name: string;
  amount: string;
  currency: string;
  cadence: string;
  cycles: number;
  start_date: string;
  last_due_date?: string;
  quorum: string;
  grace_days: number;
  buffer_seed: string;
  remedy_menu: string[];
  rotation?: string[];
  languages?: string[];
}

export interface InviteRow {
  handle: string;
  name: string;
  token: string;
  url: string;
  vaulted: boolean;
  join_status: string;
  expires_at: string;
}

export interface ApprovalRecord {
  circle_id: string;
  charter_sha256: string;
  approved_by: string;
  approved_at: string;
  draft_id: string | null;
}

export interface FreezeResult {
  ok: boolean;
  circle_id: string;
  draft_id: string | null;
  matches_circle: boolean;
  confirmed: boolean;
  created_circle: boolean;
  charter_sha256: string;
  canonical_json?: string;
  billing_plan: BillingPlan;
  summary: CharterSummary;
  fees: FeesInfo;
  approval: ApprovalRecord | null;
  invites?: InviteRow[];
}

export interface JoinCard {
  ok: boolean;
  circle: {
    id: string;
    name: string;
    amount: string;
    currency: string;
    cadence: string;
    cycles: number;
    start_date: string;
    fee_policy: string;
    fee_note: string;
    fees: FeesInfo;
    charter_sha256: string;
    buffer_seed: string;
    quorum: string;
    grace_days: number;
    remedy_menu: string[];
    members: { handle: string; name: string }[];
    billing_plan: BillingPlan;
    kind: string;
    status: string;
  };
  member: { handle: string; name: string; lang: string };
  vaulted: boolean;
  join: {
    handle: string;
    name: string;
    lang: string;
    payout_round: number | null;
    payout_date: string | null;
    payout_wallet: string;
    payout_receiver_masked: string | null;
    join_status: string;
    join_message: string;
  };
  invite: { expires_at: string };
  paypal_ready: boolean;
  consent: string;
}

export interface JudgeCircleResult {
  ok: boolean;
  circle_id: string;
  invite: { handle: string; token: string; url: string; path: string; expires_at: string };
  paypal_ready: boolean;
  payee_configured: boolean;
  notes: string[];
}


// ---------------------------------------------------------------------------------------------
// Member mandates and member agents (docs/CONTRACTS.md "Member mandates"; flag `member_mandates`, default OFF)
// ---------------------------------------------------------------------------------------------

export interface MemberMandateRules {
  accept_earlier_swap: boolean;
  max_advance: string;
  require_ask_for_later_swap: boolean;
  summary: string;
}

/** One member's signed mandate as `GET /api/circles/{id}/mandates` reports it. */
export interface MemberMandateInfo {
  member: string;
  name: string;
  present: boolean;
  signature_verified: boolean;
  /** The verification reason from the server ("ok", "expired", ...). */
  status: string;
  max_charge_per_round: string | null;
  max_advance: string | null;
  expires: string | null;
  approved_at: string | null;
  charter_sha256: string | null;
  rules: MemberMandateRules | null;
  /** First 16 hex chars of the HMAC plus an ellipsis; never the whole signature. */
  signature: string | null;
}

export interface MandatesView {
  circle_id: string;
  enabled: boolean;
  charter_sha256: string;
  /** "AP2-style Intent Mandate, our own implementation; ..." (server text, shown as is). */
  label: string;
  /** "server-attested HMAC signature, not a key held by the member". */
  signature_label: string;
  members: MemberMandateInfo[];
}

export type AgentKind = "consent" | "vote";
export type AgentChoice = "yes" | "no" | "ask";
/** Who recorded the answer: the member's agent, the member (a human's tap) or the judge's click. */
export type AnswerSource = "agent" | "member" | "judge";

/** One entry of `decisions[]` from `POST .../member-agents/run` and the Story B steps. */
export interface AgentDecision {
  member: string;
  kind: AgentKind;
  choice: AgentChoice;
  reason: string;
  /** Decision-table row, e.g. "swap_earlier". */
  rule: string;
  explanation?: string | null;
  explanation_mode?: string | null;
  trace?: string[];
  recorded: boolean;
  source: AnswerSource | null;
  recorded_choice?: "yes" | "no";
  asked: boolean;
}

/** `POST /api/judge/try_overcharge`: the policy engine's answer; PayPal is never called. */
export interface OverchargeResult {
  ok?: boolean;
  denied: boolean;
  reason?: string;
  banner?: string;
  member?: string;
  amount?: string;
  round_n?: number;
  paypal_called?: boolean;
}
