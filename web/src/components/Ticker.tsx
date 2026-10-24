import { useEffect, useState } from "react";
import type { TickerInfo } from "../types";
import { fmtTime, parseDateTime } from "../format";

export interface TickerOptions {
  /** Wall-clock now, in ms. */
  nowMs: number;
  /** A payout step is running right now (story step `payout` or `clean_round4`). */
  running: boolean;
  /** The paid round is seeded fixture history (REPLAY- ids), not a measured payout. */
  seeded: boolean;
}

export interface TickerView {
  /** "4.3 s", or "—" when nothing is being measured. */
  value: string;
  sub: string;
  tone: "live" | "done" | "idle";
  /** Announced to screen readers only when the measurement is final. */
  announce: string | null;
}

/**
 * The "last contribution -> pot paid" ticker, from server timestamps only.
 *
 * Paid: the server's seconds (or paid_at - last_capture_at). While a payout task runs: wall-clock now
 * minus the server's capture timestamp, so a reload never restarts the count. Otherwise it shows "—":
 * a capture that is not being paid out is not counted, however long it waits.
 */
export function tickerView(t: TickerInfo | null, opts: TickerOptions): TickerView {
  const idle = (sub: string): TickerView => ({ value: "—", sub, tone: "idle", announce: null });
  if (!t) return idle("waiting for the first capture of the round");
  if (t.paid_at) {
    let seconds: number | null = typeof t.seconds === "number" ? Math.max(0, t.seconds) : null;
    if (seconds === null) {
      const a = parseDateTime(t.last_capture_at)?.getTime();
      const b = parseDateTime(t.paid_at)?.getTime();
      seconds = a !== undefined && b !== undefined ? Math.max(0, (b - a) / 1000) : null;
    }
    if (seconds === null) return idle(`paid at ${fmtTime(t.paid_at)}`);
    const value = `${seconds.toFixed(1)} s`;
    const sub = opts.seeded ? "seeded history · no PayPal call" : `paid at ${fmtTime(t.paid_at)}`;
    return { value, sub, tone: "done", announce: opts.seeded ? null : `Pot paid ${value} after the last contribution` };
  }
  if (t.last_capture_at) {
    const captured = parseDateTime(t.last_capture_at)?.getTime();
    if (opts.running && captured !== undefined) {
      const seconds = Math.max(0, (opts.nowMs - captured) / 1000);
      return {
        value: `${seconds.toFixed(1)} s`,
        sub: `payout running · since the capture at ${fmtTime(t.last_capture_at)}`,
        tone: "live",
        announce: null,
      };
    }
    return idle(`waiting for payout (last capture ${fmtTime(t.last_capture_at)})`);
  }
  return idle("waiting for the first capture of the round");
}

export interface TickerStatProps {
  ticker: TickerInfo | null;
  running: boolean;
  seeded: boolean;
}

/** One stat tile (lives inside the Counters card). */
export function TickerStat({ ticker, running, seeded }: TickerStatProps) {
  const counting = running && Boolean(ticker?.last_capture_at && !ticker?.paid_at);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!counting) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [counting]);

  const view = tickerView(ticker, { nowMs: now, running, seeded });
  return (
    <div className={`tk-stat tk-ticker tk-ticker-${view.tone}`} aria-label="Payout ticker">
      <span className="tk-stat-label">Last contribution → pot paid</span>
      <span className="tk-stat-value">{view.value}</span>
      <span className="tk-stat-sub">{view.sub}</span>
      <span className="tk-visually-hidden" aria-live="polite">
        {view.announce ?? ""}
      </span>
    </div>
  );
}
