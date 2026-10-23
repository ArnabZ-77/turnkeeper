/**
 * Page shell (docs/CONTRACTS.md B5).
 *
 * Load order: GET /api/session -> GET /api/circles/{id} -> EventSource /api/sessions/{sid}/events.
 * SSE events fold into the reducer in state.ts; a `board` event bumps `boardVersion`, which the
 * effect below turns into a refetch. Judge buttons call `/api/judge/*` and the round routes.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApiError, api, describeError } from "./api";
import { openEventStream } from "./events";
import { initialState, nextRound, reducer } from "./state";
import type { JudgeResult, SessionInfo, StoryStepResult, TaskStatus, ToastLevel } from "./types";
import { MODE_HELP, hasActiveSet, modeChip, nameOf, paypalChip, recoveryButtons } from "./recovery";
import { PhoneInbox } from "./components/PhoneInbox";
import { RecoveryPanel } from "./components/RecoveryPanel";
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
import { ForecastCard } from "./components/ForecastCard";
import { StoryBar } from "./components/StoryBar";
import { Link } from "./components/Link";
import { navigate } from "./routes";

const WAITLIST_POLL_MS = 10_000;
const TASK_POLL_MS = 1_500;
const TASK_POLL_MAX = 160; // ~4 minutes
const MOVED_HIGHLIGHT_MS = 4_000;
const NO_MOVES: string[] = [];
const STORY_POLL_MS = 1_500;
const ADVANCED_KEY = "tk-advanced-open";

function readAdvancedOpen(): boolean {
  try {
    return localStorage.getItem(ADVANCED_KEY) === "1";
  } catch {
    return false;
  }
}

function taskIdOf(r: unknown): string | null {
  if (typeof r !== "object" || r === null) return null;
  const id = (r as { task_id?: unknown }).task_id;
  return typeof id === "string" && id ? id : null;
}

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
  /** Bumped on reset/unmount so task polls from an old run stop. */
  const pollGen = useRef(0);
  useEffect(
    () => () => {
      pollGen.current += 1;
    },
    [],
  );

  const toast = useCallback((level: ToastLevel, text: string) => dispatch({ type: "toast", level, text }), []);

  // 1. Session (sets the tk_sid cookie and clones the fixture circle on first visit).
  const loadSession = useCallback(async () => {
    try {
      const session = await api.getSession();
      dispatch({ type: "session", session });
    } catch (e) {
      if (e instanceof ApiError && e.waitlist) {
        dispatch({ type: "waitlist", on: true });
        dispatch({ type: "replayUrl", url: e.replayUrl });
      } else dispatch({ type: "error", message: `Could not load the session: ${describeError(e)}` });
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
        if (!alive) return;
        // 403: the session was reset in another tab and owns a different circle now.
        if (e instanceof ApiError && (e.status === 403 || e.status === 401)) void loadSession();
        else dispatch({ type: "error", message: `Could not load the circle: ${describeError(e)}` });
      });
    api
      .getRecovery(circleId)
      .then((recovery) => {
        if (alive) dispatch({ type: "recovery", recovery });
      })
      .catch(() => {
        /* older server without the recovery route, or a reset in flight: the board's block is enough */
      });
    return () => {
      alive = false;
    };
  }, [circleId, state.boardVersion, loadSession]);

  // Judge Mode story: refreshed with the board, and polled while a background step runs.
  const loadStory = useCallback(async () => {
    try {
      dispatch({ type: "story", story: await api.story.get() });
    } catch {
      /* older server without the story route, or a reset in flight: the advanced controls still work */
    }
  }, []);
  useEffect(() => {
    if (circleId) void loadStory();
  }, [circleId, state.boardVersion, loadStory]);
  const storyRunning = Boolean(state.story?.running);
  useEffect(() => {
    if (!storyRunning) return;
    const id = setInterval(() => void loadStory(), STORY_POLL_MS);
    return () => clearInterval(id);
  }, [storyRunning, loadStory]);

  const [advancedOpen, setAdvancedOpen] = useState(readAdvancedOpen);
  const onToggleAdvanced = useCallback((open: boolean) => {
    setAdvancedOpen(open);
    try {
      localStorage.setItem(ADVANCED_KEY, open ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
  }, []);

  // The swap highlight fades after a few seconds.
  const movedSeq = state.moved?.seq ?? 0;
  useEffect(() => {
    if (!movedSeq) return;
    const id = setTimeout(() => dispatch({ type: "clearMoved" }), MOVED_HIGHLIGHT_MS);
    return () => clearTimeout(id);
  }, [movedSeq]);

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

  /** Follow a background task until it finishes; its `mode` is the truth about the run. */
  const watchTask = useCallback(
    (taskId: string) => {
      const gen = pollGen.current;
      let tries = 0;
      const tick = async () => {
        if (gen !== pollGen.current) return;
        tries += 1;
        let status: TaskStatus | null = null;
        try {
          status = await api.getTask(taskId);
        } catch (e) {
          if (e instanceof ApiError && e.status === 404) return; // reset, or another session's task
        }
        if (gen !== pollGen.current) return;
        if (status?.done) {
          dispatch({ type: "runMode", mode: status.mode });
          if (status.ok === false && (status.error || status.summary)) {
            toast("warn", `${status.kind} finished: ${status.error ?? status.summary}`);
          }
          return;
        }
        if (tries < TASK_POLL_MAX) setTimeout(() => void tick(), TASK_POLL_MS);
      };
      setTimeout(() => void tick(), TASK_POLL_MS);
    },
    [toast],
  );

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
        const taskId = taskIdOf(result);
        if (taskId) watchTask(taskId);
      } catch (e) {
        if (e instanceof ApiError && e.waitlist) dispatch({ type: "waitlist", on: true });
        toast("error", describeError(e));
      } finally {
        busyRef.current = false;
        dispatch({ type: "busy", busy: false });
      }
    },
    [toast, watchTask],
  );

  const onReset = useCallback(() => {
    if (!circleId) return;
    void run(
      () => api.judge.reset(circleId),
      (r) => {
        pollGen.current += 1;
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
  const onSimulateDecline = useCallback(() => {
    if (circleId)
      void run(
        () => api.judge.simulateRound3Decline(circleId),
        () => toast("info", "Round 3 simulated: Ravi's charge declined (no PayPal call)"),
      );
  }, [circleId, run, toast]);
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

  const onReplyAsRavi = useCallback(() => {
    if (!circleId) return;
    void run(
      () => api.judge.replyAsRavi(circleId),
      (r) => {
        const res = r as JudgeResult;
        const msg = res.message as { id?: unknown } | undefined;
        if (msg && typeof msg.id === "number" && typeof res.translation === "string") {
          dispatch({ type: "translation", id: msg.id, text: res.translation });
        }
        if (typeof res.payday === "string") toast("info", `Ravi named a payday: ${res.payday}`);
      },
    );
  }, [circleId, run, toast]);
  const onStartRecovery = useCallback(() => {
    if (circleId) void run(() => api.startRecovery(circleId), () => toast("info", "Recovery started"));
  }, [circleId, run, toast]);
  const onConsent = useCallback(() => {
    if (circleId) void run(() => api.judge.consentAsAffected(circleId));
  }, [circleId, run]);
  const onVote = useCallback(() => {
    if (circleId) void run(() => api.judge.voteAsMembers(circleId));
  }, [circleId, run]);
  const onApprove = useCallback(() => {
    if (circleId) void run(() => api.judge.approveAsPriya(circleId), () => toast("success", "Priya approved: remedies executing"));
  }, [circleId, run, toast]);
  const onMarkPaid = useCallback(() => {
    if (circleId) void run(() => api.judge.markInvoicePaid(circleId));
  }, [circleId, run]);

  const afterStory = useCallback(
    (r: unknown) => {
      const res = r as StoryStepResult;
      if (res?.session && isSessionInfo(res.session)) {
        pollGen.current += 1;
        dispatch({ type: "resetRun" });
        dispatch({ type: "session", session: res.session });
      }
      if (res?.story) dispatch({ type: "story", story: res.story });
      if (res?.summary && res.step !== "reset") toast(res.task_id ? "info" : "success", res.summary);
    },
    [toast],
  );
  const onStoryNext = useCallback(
    (step: string) => {
      void run(() => api.story.run(step) as unknown as Promise<JudgeResult>, afterStory);
    },
    [run, afterStory],
  );
  const onStoryRestart = useCallback(() => {
    void run(() => api.story.run("reset") as unknown as Promise<JudgeResult>, afterStory);
  }, [run, afterStory]);
  const onReroute = useCallback(
    (roundN: number) => {
      if (circleId)
        void run(
          () => api.judge.rerouteUnclaimed(circleId, roundN),
          () => toast("info", `Re-routing round ${roundN}: waiting for the batch (up to ~60 s), then one cancel attempt`),
        );
    },
    [circleId, run, toast],
  );
  const onRunForecast = useCallback(() => {
    if (circleId) void run(() => api.judge.runForecast(circleId));
  }, [circleId, run]);
  const onJudgeCircle = useCallback(() => {
    void run(
      () => api.join.judgeCircle() as unknown as Promise<JudgeResult>,
      (r) => {
        const path = (r as { invite?: { path?: unknown } })?.invite?.path;
        if (typeof path === "string") navigate(path);
      },
    );
  }, [run]);

  const onDismissToast = useCallback((id: number) => dispatch({ type: "dismissToast", id }), []);
  const onClearTrace = useCallback(() => dispatch({ type: "clearTrace" }), []);

  const board = state.board;
  const session = state.session;
  const next = useMemo(() => nextRound(board), [board]);
  const members = board?.members ?? [];
  const rounds = board?.rounds ?? [];
  const recovery = state.recovery;
  const recButtons = useMemo(() => recoveryButtons(board, recovery), [board, recovery]);
  const aiChip = modeChip(state.lastMode);
  const ppChip = session ? paypalChip(session) : null;
  const inbox = recovery?.inbox ?? [];
  const phoneMember = recovery?.member ?? inbox[0]?.member ?? null;
  const showRecovery = hasActiveSet(recovery);
  const rerouteRound = rounds.find((r) => (r.payout?.status ?? "").toUpperCase() === "UNCLAIMED")?.n ?? null;

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
                {ppChip && (
                  <span className={`tk-chip ${ppChip.cls}`} title={ppChip.tip}>
                    <span className="tk-dot" />
                    {ppChip.label}
                  </span>
                )}
                <span
                  className={`tk-chip ${aiChip ? aiChip.cls : "tk-chip-muted"}`}
                  title={`${aiChip ? aiChip.tip : "No treasurer run yet in this session."}\n\n${MODE_HELP}`}
                >
                  <span className="tk-dot" />
                  AI: {aiChip ? aiChip.label : "no run yet"}
                </span>
                {session.frozen && <span className="tk-chip tk-chip-warn">frozen</span>}
              </>
            )}
            <Link className="tk-chip tk-chip-nav" href="/compile" title="Paste a circle's rules and compile them into a frozen charter">
              Charter compiler →
            </Link>
            <button type="button" className="tk-chip" onClick={toggle} aria-label="Toggle light/dark theme">
              {resolved === "dark" ? <IconSun /> : <IconMoon />}
              {resolved === "dark" ? "light" : "dark"}
            </button>
          </div>
        </div>
        {state.waitlist && (
          <div className="tk-banner" role="status">
            All demo seats are busy right now. You are on the waitlist; this page retries every {WAITLIST_POLL_MS / 1000} s.
            {state.replayUrl && (
              <>
                {" "}
                Meanwhile, <a href={state.replayUrl}>the recorded Judge Mode run</a> is open to everyone.
              </>
            )}
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
        {session && session.vault_seeded === false && (
          <div className="tk-banner" role="status">
            Members not vaulted: set TK_SEED_VAULT_IDS (see README). Charges will read "No vault id on file";
            Judge mode offers "Simulate Round 3 decline" instead of Run Round 3.
          </div>
        )}
        <CharterCard circle={board?.circle ?? null} members={members} rounds={rounds} />
        <Counters counters={board?.counters ?? null} />
        <Ticker ticker={state.ticker} />
      </header>

      <main className="tk-main">
        <RotationBoard board={board} moved={state.moved?.handles ?? NO_MOVES} />
        {showRecovery && recovery && (
          <RecoveryPanel recovery={recovery} board={board} busy={state.busy} approveBlocked={recButtons.approve} onApprove={onApprove} />
        )}
      </main>

      <aside className="tk-rail">
        <ForecastCard board={board} />
        {phoneMember && (
          <PhoneInbox memberName={nameOf(board, phoneMember)} messages={inbox} translations={state.translations} />
        )}
        <AgentTrace entries={state.trace} mode={state.lastMode} keyConfigured={Boolean(session?.live_ai)} onClear={onClearTrace} />
        <PayoutReceipt
          payout={state.payout}
          rounds={rounds}
          members={members}
          currency={board?.circle.currency}
          onReroute={onReroute}
          busy={state.busy}
        />
        <QueueBadge position={state.queuePosition} connection={state.connection} />
      </aside>

      <footer className="tk-judge">
        <StoryBar story={state.story} busy={state.busy} onNext={onStoryNext} onRestart={onStoryRestart} />
        <JudgeBar
          open={advancedOpen}
          onToggleOpen={onToggleAdvanced}
          onRunForecast={onRunForecast}
          rerouteRound={rerouteRound}
          onReroute={onReroute}
          onJudgeCircle={onJudgeCircle}
          circle={board?.circle ?? null}
          busy={state.busy}
          paypalLive={Boolean(session?.paypal_live)}
          vaultSeeded={session?.vault_seeded !== false}
          nextRound={next}
          recovery={recButtons}
          onReset={onReset}
          onAdvance={onAdvance}
          onRunRound={onRunRound}
          onSimulateDecline={onSimulateDecline}
          onToggleRavi={onToggleRavi}
          onUnclaimed={onUnclaimed}
          onCollect={onCollect}
          onPayout={onPayout}
          onReplyAsRavi={onReplyAsRavi}
          onStartRecovery={onStartRecovery}
          onConsent={onConsent}
          onVote={onVote}
          onApprove={onApprove}
          onMarkPaid={onMarkPaid}
        />
      </footer>

      <Toasts toasts={state.toasts} onDismiss={onDismissToast} />
    </div>
  );
}
