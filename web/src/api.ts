/**
 * Typed fetchers for the routes in docs/CONTRACTS.md (B4 owns the server side).
 *
 * Every call is same-origin: the `tk_sid` cookie identifies the session, so nothing here needs a
 * token. Errors become `ApiError` with the HTTP status and parsed body; `waitlist` flags the
 * 429 `{"waitlist": true}` response the session cap returns.
 */

import type { BoardModel, JudgeResult, RecoveryInfo, SessionInfo, TaskStarted, TaskStatus } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  get waitlist(): boolean {
    return this.status === 429 && isRecord(this.body) && this.body.waitlist === true;
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** One-line description of any thrown value, for toasts. */
export function describeError(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

function messageFor(status: number, data: unknown): string {
  if (isRecord(data)) {
    const detail = data.detail ?? data.error ?? data.message ?? data.denied;
    if (typeof detail === "string") return detail;
    if (isRecord(detail) && typeof detail.message === "string") return detail.message;
    if (detail !== undefined) return JSON.stringify(detail);
    if (data.waitlist === true) return "All demo seats are busy right now; you are on the waitlist";
  }
  if (typeof data === "string" && data.trim()) return data.slice(0, 200);
  return `HTTP ${status}`;
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method, headers, credentials: "same-origin" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) throw new ApiError(res.status, messageFor(res.status, data), data);
  return data as T;
}

const enc = encodeURIComponent;

export type JudgeAction =
  | "reset"
  | "advance_clock"
  | "run_round"
  | "toggle_ravi_declines"
  | "make_next_payout_unclaimed"
  // B6 recovery path (app/routes/recovery.py)
  | "reply_as_ravi"
  | "vote_as_members"
  | "consent_as_affected"
  | "approve_as_priya"
  | "mark_invoice_paid"
  // No live sandbox (no credentials or no seeded vault ids): stands in for Run Round 3
  | "simulate_round3_decline";

/** `POST /api/judge/{action}` with `{circle_id, ...extra}`. */
export function judge(action: JudgeAction, circleId: string, extra: Record<string, unknown> = {}): Promise<JudgeResult> {
  return request<JudgeResult>("POST", `/api/judge/${action}`, { circle_id: circleId, ...extra });
}

export const api = {
  getSession: (): Promise<SessionInfo> => request("GET", "/api/session"),
  resetSession: (): Promise<SessionInfo> => request("POST", "/api/session/reset"),
  getCircle: (circleId: string): Promise<BoardModel> => request("GET", `/api/circles/${enc(circleId)}`),
  collectRound: (circleId: string, roundN: number): Promise<TaskStarted> =>
    request("POST", `/api/circles/${enc(circleId)}/rounds/${roundN}/collect`),
  payoutRound: (circleId: string, roundN: number): Promise<TaskStarted> =>
    request("POST", `/api/circles/${enc(circleId)}/rounds/${roundN}/payout`),
  judge: {
    reset: (circleId: string) => judge("reset", circleId),
    advanceClock: (circleId: string, days: number) => judge("advance_clock", circleId, { days }),
    runRound: (circleId: string, roundN: number) => judge("run_round", circleId, { round_n: roundN }),
    toggleRaviDeclines: (circleId: string, on: boolean) => judge("toggle_ravi_declines", circleId, { on }),
    makeNextPayoutUnclaimed: (circleId: string, on: boolean) =>
      judge("make_next_payout_unclaimed", circleId, { on }),
    replyAsRavi: (circleId: string, text?: string) => judge("reply_as_ravi", circleId, text ? { text } : {}),
    voteAsMembers: (circleId: string) => judge("vote_as_members", circleId),
    consentAsAffected: (circleId: string) => judge("consent_as_affected", circleId),
    approveAsPriya: (circleId: string) => judge("approve_as_priya", circleId),
    markInvoicePaid: (circleId: string) => judge("mark_invoice_paid", circleId),
    simulateRound3Decline: (circleId: string) => judge("simulate_round3_decline", circleId),
  },
  getTask: (taskId: string): Promise<TaskStatus> => request("GET", `/api/tasks/${enc(taskId)}`),
  getRecovery: (circleId: string): Promise<RecoveryInfo> => request("GET", `/api/circles/${enc(circleId)}/recovery`),
  startRecovery: (circleId: string, body: { round_n?: number; member?: string } = {}): Promise<TaskStarted> =>
    request("POST", `/api/circles/${enc(circleId)}/recovery/start`, body),
};

/** URL of the session's SSE stream. */
export function eventsUrl(sid: string): string {
  return `/api/sessions/${enc(sid)}/events`;
}
