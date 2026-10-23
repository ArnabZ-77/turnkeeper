/**
 * Wire types for the Turnkeeper UI.
 *
 * These mirror docs/CONTRACTS.md: the BoardModel served by `GET /api/circles/{id}` and the SSE
 * payloads from `GET /api/sessions/{sid}/events`. Money is always a `"200.00"` string on the wire;
 * the UI formats it and never does arithmetic on it.
 */

export type ContributionStatus = "PENDING" | "COMPLETED" | "DECLINED" | "COVERED";
export type RoundStatus = "PENDING" | "COLLECTING" | "COLLECTED" | "PAID" | "VOID";
export type Wallet = "PAYPAL" | "VENMO";
export type Cadence = "monthly" | "weekly";

export interface CircleFlags {
  ravi_declines: boolean;
  next_payout_unclaimed: boolean;
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
}

export interface SessionInfo {
  sid: string;
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
  voters?: { member: string; choice: string }[];
}

export interface ConsentsInfo {
  needed: string[];
  given: string[];
  refused: string[];
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
] as const;
