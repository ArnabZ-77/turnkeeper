/**
 * UI state and the pure reducer that folds SSE events into it.
 *
 * Keeping this pure (no fetches, no timers) means the App can treat a `board` event as "bump
 * `boardVersion`" and refetch in an effect, and the whole event handling is unit-testable.
 */

import type { ConnectionState } from "./events";
import type {
  BoardModel,
  ContributionInfo,
  PayoutEvent,
  SessionInfo,
  TickerInfo,
  ToastLevel,
  TraceKind,
  TurnkeeperEvent,
} from "./types";

export interface TraceEntry {
  id: number;
  ts: string;
  kind: TraceKind;
  text: string;
  tool?: string;
  args?: unknown;
  ms?: number;
}

export interface Toast {
  id: number;
  level: ToastLevel;
  text: string;
  ts: string;
}

export interface UiState {
  session: SessionInfo | null;
  board: BoardModel | null;
  /** Incremented by `board` events; the App refetches when it changes. */
  boardVersion: number;
  trace: TraceEntry[];
  payout: PayoutEvent | null;
  ticker: TickerInfo;
  queuePosition: number;
  toasts: Toast[];
  connection: ConnectionState;
  busy: boolean;
  waitlist: boolean;
  error: string | null;
}

export type UiAction =
  | { type: "session"; session: SessionInfo }
  | { type: "board"; board: BoardModel }
  | { type: "event"; event: TurnkeeperEvent }
  | { type: "toast"; level: ToastLevel; text: string }
  | { type: "dismissToast"; id: number }
  | { type: "clearTrace" }
  /** Judge "reset": the circle is re-cloned, so everything derived from the old run is dropped. */
  | { type: "resetRun" }
  | { type: "connection"; state: ConnectionState }
  | { type: "busy"; busy: boolean }
  | { type: "waitlist"; on: boolean }
  | { type: "error"; message: string | null };

export const TRACE_LIMIT = 500;
export const TOAST_LIMIT = 6;

export const initialState: UiState = {
  session: null,
  board: null,
  boardVersion: 0,
  trace: [],
  payout: null,
  ticker: { last_capture_at: null, paid_at: null, seconds: null },
  queuePosition: 0,
  toasts: [],
  connection: "closed",
  busy: false,
  waitlist: false,
  error: null,
};

let seq = 0;
const nextId = () => ++seq;

export function emptyContribution(roundN: number, member: string): ContributionInfo {
  return {
    round_n: roundN,
    member,
    status: "PENDING",
    order_id: null,
    capture_id: null,
    gross: null,
    fee: null,
    net: null,
    decline_code: null,
    decline_source: null,
    attempt: 0,
    webhook_verified: false,
    predicted: false,
    label: null,
  };
}

function pushToast(state: UiState, level: ToastLevel, text: string, ts: string): UiState {
  const toasts = [...state.toasts, { id: nextId(), level, text, ts }];
  return { ...state, toasts: toasts.slice(-TOAST_LIMIT) };
}

/** Fold one SSE event into the state. Exported for tests; the reducer delegates here. */
export function applyEvent(state: UiState, ev: TurnkeeperEvent): UiState {
  switch (ev.type) {
    case "chip": {
      const board = state.board;
      if (!board) return state;
      const contributions = [...(board.contributions ?? [])];
      const idx = contributions.findIndex((c) => c.round_n === ev.round_n && c.member === ev.member);
      const base = idx >= 0 ? contributions[idx] : emptyContribution(ev.round_n, ev.member);
      const patched: ContributionInfo = {
        ...base,
        status: ev.status ?? base.status,
        order_id: ev.order_id ?? base.order_id,
        capture_id: ev.capture_id ?? base.capture_id,
        label: ev.label ?? base.label,
        decline_code: ev.decline_code ?? base.decline_code,
        webhook_verified: ev.webhook_verified ?? base.webhook_verified,
        predicted: ev.predicted ?? base.predicted,
      };
      if (idx >= 0) contributions[idx] = patched;
      else contributions.push(patched);
      return { ...state, board: { ...board, contributions } };
    }
    case "trace": {
      const entry: TraceEntry = {
        id: nextId(),
        ts: ev.ts,
        kind: ev.kind ?? "text",
        text: typeof ev.text === "string" ? ev.text : JSON.stringify(ev.text ?? ""),
        tool: ev.tool,
        args: ev.args,
        ms: ev.ms,
      };
      const trace = [...state.trace, entry];
      return { ...state, trace: trace.length > TRACE_LIMIT ? trace.slice(-TRACE_LIMIT) : trace };
    }
    case "payout": {
      let board = state.board;
      if (board) {
        const rounds = (board.rounds ?? []).map((r) => {
          if (r.n !== ev.round_n) return r;
          const prev = r.payout;
          return {
            ...r,
            status: ev.status === "SUCCESS" ? "PAID" : r.status,
            payout: {
              batch_id: ev.batch_id ?? prev?.batch_id ?? null,
              item_id: ev.item_id ?? prev?.item_id ?? null,
              status: ev.status,
              amount: ev.amount ?? prev?.amount ?? "",
            },
          };
        });
        board = { ...board, rounds };
      }
      return { ...state, board, payout: ev };
    }
    case "ticker": {
      const ticker: TickerInfo = {
        last_capture_at: ev.last_capture_at ?? null,
        paid_at: ev.paid_at ?? null,
        seconds: typeof ev.seconds === "number" ? ev.seconds : null,
      };
      const board = state.board ? { ...state.board, ticker } : state.board;
      return { ...state, ticker, board };
    }
    case "toast":
      return pushToast(state, ev.level ?? "info", String(ev.text ?? ""), ev.ts);
    case "queue":
      return { ...state, queuePosition: Math.max(0, Number(ev.position) || 0) };
    case "board":
      return { ...state, boardVersion: state.boardVersion + 1 };
    case "schedule_changed": {
      const board = state.board;
      if (!board || !Array.isArray(ev.rotation)) return state;
      const rotation = ev.rotation;
      const members = board.members.map((m) => {
        const pos = rotation.indexOf(m.handle);
        return pos >= 0 ? { ...m, rotation_pos: pos + 1 } : m;
      });
      const rounds = board.rounds.map((r) => {
        const recipient = rotation[r.n - 1];
        return recipient && r.status !== "PAID" ? { ...r, recipient } : r;
      });
      return { ...state, board: { ...board, circle: { ...board.circle, rotation }, members, rounds } };
    }
    case "unknown":
    default:
      return state;
  }
}

export function reducer(state: UiState, action: UiAction): UiState {
  switch (action.type) {
    case "session":
      return { ...state, session: action.session, waitlist: false, error: null };
    case "board":
      return { ...state, board: action.board, ticker: action.board.ticker ?? state.ticker, error: null };
    case "event":
      return applyEvent(state, action.event);
    case "toast":
      return pushToast(state, action.level, action.text, new Date().toISOString());
    case "dismissToast":
      return { ...state, toasts: state.toasts.filter((t) => t.id !== action.id) };
    case "clearTrace":
      return { ...state, trace: [] };
    case "resetRun":
      return {
        ...state,
        board: null,
        trace: [],
        payout: null,
        ticker: initialState.ticker,
        queuePosition: 0,
        error: null,
      };
    case "connection":
      return { ...state, connection: action.state };
    case "busy":
      return { ...state, busy: action.busy };
    case "waitlist":
      return { ...state, waitlist: action.on };
    case "error":
      return { ...state, error: action.message };
    default:
      return state;
  }
}

/** The lowest-numbered round that is not yet PAID or VOID (what "Run round N" should target). */
export function nextRound(board: BoardModel | null): number | null {
  if (!board) return null;
  const open = board.rounds.filter((r) => r.status !== "PAID" && r.status !== "VOID").sort((a, b) => a.n - b.n);
  return open.length ? open[0].n : null;
}
