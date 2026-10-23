/**
 * Page shell (docs/CONTRACTS.md B5).
 *
 * Load order: GET /api/session -> GET /api/circles/{id} -> EventSource /api/sessions/{sid}/events.
 * SSE events fold into the reducer in state.ts; a `board` event bumps `boardVersion`, which the
 * effect below turns into a refetch. Judge buttons call `/api/judge/*` and the round routes.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { ApiError, api, describeError } from "./api";
import { openEventStream } from "./events";
import { initialState, nextRound, reducer } from "./state";
import type { JudgeResult, SessionInfo, ToastLevel } from "./types";
import { useTheme } from "./theme/useTheme";
import { AgentTrace } from "./components/AgentTrace";
import { CharterCard } from "./components/CharterCard";
import { Counters } from "./components/Counters";
import { JudgeBar } from "./components/JudgeBar";
import { PayoutReceipt } from "./components/PayoutReceipt";
import { QueueBadge } from "./components/QueueBadge";
import { Ticker } from "./components/Ticker";
import { Toasts } from "./components/Toasts";
import { IconMoon, IconSun } from "./components/icons";
import { RotationBoard } from "./board/RotationBoard";

const WAITLIST_POLL_MS = 10_000;

function isSessionInfo(v: unknown): v is SessionInfo {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as SessionInfo).sid === "string" &&
    typeof (v as SessionInfo).circle_id === "string"
  );
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const { resolved, toggle } = useTheme();
  const busyRef = useRef(false);

  const toast = useCallback((level: ToastLevel, text: string) => dispatch({ type: "toast", level, text }), []);

  // 1. Session (sets the tk_sid cookie and clones the fixture circle on first visit).
  const loadSession = useCallback(async () => {
    try {
      const session = await api.getSession();
      dispatch({ type: "session", session });
    } catch (e) {
      if (e instanceof ApiError && e.waitlist) dispatch({ type: "waitlist", on: true });
      else dispatch({ type: "error", message: `Could not load the session: ${describeError(e)}` });
    }
  }, []);

  useEffect(() => {
    void loadSession();
  }, [loadSession]);

  useEffect(() => {
    if (!state.waitlist) return;
    const id = setInterval(() => void loadSession(), WAITLIST_POLL_MS);
    return () => clearInterval(id);
  }, [state.waitlist, loadSession]);

  // 2. Board, refetched whenever the circle changes or a `board` event arrives.
  const circleId = state.session?.circle_id ?? null;
  useEffect(() => {
    if (!circleId) return;
    let alive = true;
    api
      .getCircle(circleId)
      .then((board) => {
        if (alive) dispatch({ type: "board", board });
      })
      .catch((e) => {
        if (alive) dispatch({ type: "error", message: `Could not load the circle: ${describeError(e)}` });
      });
    return () => {
      alive = false;
    };
  }, [circleId, state.boardVersion]);

  // 3. Live events for this session.
  const sid = state.session?.sid ?? null;
  useEffect(() => {
    if (!sid) return;
    const stream = openEventStream(sid, {
      onEvent: (event) => dispatch({ type: "event", event }),
      onState: (connection) => dispatch({ type: "connection", state: connection }),
    });
    return () => stream.close();
  }, [sid]);

  // Judge actions: one in flight at a time; errors become toasts, never a blank page.
  const run = useCallback(
    async (fn: () => Promise<JudgeResult | { task_id: string } | SessionInfo>, after?: (r: unknown) => void) => {
      if (busyRef.current) return;
      busyRef.current = true;
      dispatch({ type: "busy", busy: true });
      try {
        const result = await fn();
        if (result && typeof result === "object" && (result as JudgeResult).ok === false) {
          const r = result as JudgeResult;
          toast("warn", String(r.denied ?? r.error ?? r.message ?? "Action refused"));
        }
        after?.(result);
      } catch (e) {
        if (e instanceof ApiError && e.waitlist) dispatch({ type: "waitlist", on: true });
        toast("error", describeError(e));
      } finally {
        busyRef.current = false;
        dispatch({ type: "busy", busy: false });
      }
    },
    [toast],
  );

  const onReset = useCallback(() => {
    if (!circleId) return;
    void run(
      () => api.judge.reset(circleId),
      (r) => {
        dispatch({ type: "resetRun" });
        const session = (r as JudgeResult | undefined)?.session;
        if (isSessionInfo(session)) dispatch({ type: "session", session });
        else void loadSession();
      },
    );
  }, [circleId, run, loadSession]);

  const onAdvance = useCallback(
    (days: number) => {
      if (circleId) void run(() => api.judge.advanceClock(circleId, days));
    },
    [circleId, run],
  );
  const onRunRound = useCallback(
    (roundN: number) => {
      if (circleId) void run(() => api.judge.runRound(circleId, roundN), () => toast("info", `Round ${roundN} started`));
    },
    [circleId, run, toast],
  );
  const onToggleRavi = useCallback(
    (on: boolean) => {
      if (circleId) void run(() => api.judge.toggleRaviDeclines(circleId, on));
    },
    [circleId, run],
  );
  const onUnclaimed = useCallback(
    (on: boolean) => {
      if (circleId) void run(() => api.judge.makeNextPayoutUnclaimed(circleId, on));
    },
    [circleId, run],
  );
  const onCollect = useCallback(
    (roundN: number) => {
      if (circleId) void run(() => api.collectRound(circleId, roundN), () => toast("info", `Collecting round ${roundN}`));
    },
    [circleId, run, toast],
  );
  const onPayout = useCallback(
    (roundN: number) => {
      if (circleId) void run(() => api.payoutRound(circleId, roundN), () => toast("info", `Paying round ${roundN}`));
    },
    [circleId, run, toast],
  );

  const onDismissToast = useCallback((id: number) => dispatch({ type: "dismissToast", id }), []);
  const onClearTrace = useCallback(() => dispatch({ type: "clearTrace" }), []);

  const board = state.board;
  const session = state.session;
  const next = useMemo(() => nextRound(board), [board]);
  const members = board?.members ?? [];
  const rounds = board?.rounds ?? [];

  return (
    <div className="tk-app">
      <header className="tk-header">
        <div className="tk-topline" style={{ gridColumn: "1 / -1", marginBottom: 0 }}>
          <div className="tk-brand">
            <h1>Turnkeeper</h1>
            <span className="tk-tagline">AI treasurer for rotating savings circles, on the PayPal sandbox</span>
          </div>
          <div className="tk-chips">
            {session && (
              <>
                <span className="tk-chip" title="Idempotency run id (prefixes every PayPal-Request-Id)">
                  run <code>{session.run_id}</code>
                </span>
                <span className="tk-chip" title="Circle id">
                  circle <code>{session.circle_id}</code>
                </span>
                <span className={`tk-chip ${session.paypal_live ? "tk-chip-good" : "tk-chip-pending"}`}>
                  <span className="tk-dot" />
                  {session.paypal_live ? "PayPal sandbox" : "PayPal replay"}
                </span>
                {session.frozen && <span className="tk-chip tk-chip-warn">frozen</span>}
              </>
            )}
            <button type="button" className="tk-chip" onClick={toggle} aria-label="Toggle light/dark theme">
              {resolved === "dark" ? <IconSun /> : <IconMoon />}
              {resolved === "dark" ? "light" : "dark"}
            </button>
          </div>
        </div>
        {state.waitlist && (
          <div className="tk-banner" role="status">
            All demo seats are busy right now. You are on the waitlist; this page retries every {WAITLIST_POLL_MS / 1000} s.
          </div>
        )}
        {state.error && !state.waitlist && (
          <div className="tk-banner tk-banner-error" role="alert">
            {state.error}{" "}
            <button type="button" className="tk-btn tk-btn-sm" onClick={() => void loadSession()}>
              retry
            </button>
          </div>
        )}
        <CharterCard circle={board?.circle ?? null} members={members} rounds={rounds} />
        <Counters counters={board?.counters ?? null} />
        <Ticker ticker={state.ticker} />
      </header>

      <main className="tk-main">
        <RotationBoard board={board} />
      </main>

      <aside className="tk-rail">
        <AgentTrace entries={state.trace} liveAi={session ? session.live_ai : null} onClear={onClearTrace} />
        <PayoutReceipt payout={state.payout} rounds={rounds} members={members} currency={board?.circle.currency} />
        <QueueBadge position={state.queuePosition} connection={state.connection} />
      </aside>

      <footer className="tk-judge">
        <JudgeBar
          circle={board?.circle ?? null}
          busy={state.busy}
          nextRound={next}
          onReset={onReset}
          onAdvance={onAdvance}
          onRunRound={onRunRound}
          onToggleRavi={onToggleRavi}
          onUnclaimed={onUnclaimed}
          onCollect={onCollect}
          onPayout={onPayout}
        />
      </footer>

      <Toasts toasts={state.toasts} onDismiss={onDismissToast} />
    </div>
  );
}
