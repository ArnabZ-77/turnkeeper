/**
 * Typed fetchers for the routes in docs/CONTRACTS.md (B4 owns the server side).
 *
 * Every call is same-origin: the `tk_sid` cookie identifies the session, so nothing here needs a
 * token. Errors become `ApiError` with the HTTP status and parsed body; `waitlist` flags the
 * 429 `{"waitlist": true}` response the session cap returns.
 */

import type {
  BoardModel,
  CompileResult,
  FreezeResult,
  JoinCard,
  JudgeCircleResult,
  JudgeResult,
  InjectionCheckView,
  RecoveryInfo,
  SessionInfo,
  StoryStepResult,
  StoryView,
  TaskStarted,
  TaskStatus,
} from "./types";

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

  /** Builder E: the waitlist 429 names the canonical recorded run (`/api/replay`). */
  get replayUrl(): string | null {
    if (!isRecord(this.body)) return null;
    const url = this.body.replay_url;
    return typeof url === "string" && url.startsWith("/") && this.body.replay_available !== false ? url : null;
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
  | "simulate_round3_decline"
  // M3 builder C: force the T-3 forecast
  | "run_forecast"
  // Builder E (D1): UNCLAIMED pot -> one cancel attempt, new batch r{rev+1} (background task)
  | "reroute_unclaimed"
  // Member mandates (flag member_mandates, default off): switch the flag, and try an over-limit charge
  | "toggle_member_mandates"
  | "try_overcharge";

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
    runForecast: (circleId: string, roundN?: number) =>
      judge("run_forecast", circleId, roundN ? { round_n: roundN } : {}),
    rerouteUnclaimed: (circleId: string, roundN?: number) =>
      judge("reroute_unclaimed", circleId, roundN ? { round_n: roundN } : {}),
    toggleMemberMandates: (circleId: string, on: boolean) => judge("toggle_member_mandates", circleId, { on }),
    /** The policy engine only: denied before PayPal is ever called. */
    tryOvercharge: (circleId: string, amount: string, member = "ravi") =>
      judge("try_overcharge", circleId, { amount, member }),
  },
  /** Member mandates and member agents; both routes answer 404 while the feature is off for the circle. */
  mandates: {
    get: (circleId: string): Promise<unknown> => request("GET", `/api/circles/${enc(circleId)}/mandates`),
    runAgents: (circleId: string, body: { set_id?: string; answers?: Record<string, string> } = {}): Promise<unknown> =>
      request("POST", `/api/circles/${enc(circleId)}/member-agents/run`, body),
  },
  /** Judge Mode story: one "Next step" button (builder E). */
  story: {
    get: (): Promise<StoryView> => request("GET", "/api/judge/story"),
    /** `variant` ("member_agents") is only valid with the reset step; omitted, the default story runs.
     * `reply` ("s06") is only valid with the reply_as_ravi step: the held-out eval's injection reply. */
    run: (step: string = "next", variant?: string, reply?: string): Promise<StoryStepResult> => {
      const q = new URLSearchParams();
      if (variant) q.set("variant", variant);
      if (reply) q.set("reply", reply);
      const qs = q.toString();
      return request("POST", `/api/judge/story/${enc(step)}${qs ? `?${qs}` : ""}`);
    },
  },
  /** Charter compiler (builder B). */
  compile: {
    demo: (lang: "en" | "hi"): Promise<{ lang: string; text: string }> =>
      request("GET", `/api/circles/compile/demo?lang=${lang}`),
    run: (text: string, lang?: string): Promise<CompileResult> =>
      request("POST", "/api/circles/compile", lang ? { text, lang } : { text }),
    freeze: (circleId: string, body: { draft_id?: string; confirm?: boolean }): Promise<FreezeResult> =>
      request("POST", `/api/circles/${enc(circleId)}/charter/freeze`, body),
  },
  /** Member join flow (builder B); no organizer session needed except for the judge circle. */
  join: {
    card: (invite: string): Promise<JoinCard> => request("GET", `/api/join/${enc(invite)}`),
    start: (invite: string): Promise<{ ok: boolean; setup_token_id: string; approve_url: string; status: string }> =>
      request("POST", `/api/join/${enc(invite)}/start`),
    wallet: (
      invite: string,
      body: { wallet: "PAYPAL" | "VENMO"; receiver?: string },
    ): Promise<{ ok: boolean; payout_wallet: string; payout_receiver_masked: string | null }> =>
      request("POST", `/api/join/${enc(invite)}/wallet`, body),
    judgeCircle: (): Promise<JudgeCircleResult> => request("POST", "/api/join/judge-circle"),
  },
  getTask: (taskId: string): Promise<TaskStatus> => request("GET", `/api/tasks/${enc(taskId)}`),
  getRecovery: (circleId: string): Promise<RecoveryInfo> => request("GET", `/api/circles/${enc(circleId)}/recovery`),
  /** Amounts the short member typed vs the proposal's, with the server-computed policy verdict (read-only). */
  injectionCheck: (circleId: string): Promise<InjectionCheckView> =>
    request("GET", `/api/circles/${enc(circleId)}/recovery/injection-check`),
  startRecovery: (circleId: string, body: { round_n?: number; member?: string } = {}): Promise<TaskStarted> =>
    request("POST", `/api/circles/${enc(circleId)}/recovery/start`, body),
};

/** URL of the session's SSE stream. The session comes from the HttpOnly cookie, never from the URL. */
export function eventsUrl(): string {
  return "/api/events";
}
