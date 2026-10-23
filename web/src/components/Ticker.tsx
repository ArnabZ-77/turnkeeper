import { useEffect, useRef, useState } from "react";
import type { TickerInfo } from "../types";
import { fmtTime, parseDateTime } from "../format";

export interface TickerProps {
  ticker: TickerInfo | null;
}

/**
 * Seconds to display for the "last contribution -> pot paid" ticker.
 *
 * Once paid, the server's `seconds` wins (falling back to the two timestamps). While a capture
 * is waiting for its payout, the UI counts up from when it first saw that capture (`sinceMs`),
 * which keeps the number sane even though the backend clock is virtual.
 */
export function tickerSeconds(t: TickerInfo | null, nowMs: number, sinceMs: number | null): number | null {
  if (!t) return null;
  if (t.paid_at) {
    if (typeof t.seconds === "number") return Math.max(0, t.seconds);
    const a = parseDateTime(t.last_capture_at)?.getTime();
    const b = parseDateTime(t.paid_at)?.getTime();
    return a !== undefined && b !== undefined ? Math.max(0, (b - a) / 1000) : null;
  }
  if (t.last_capture_at) {
    if (sinceMs !== null) return Math.max(0, (nowMs - sinceMs) / 1000);
    return typeof t.seconds === "number" ? Math.max(0, t.seconds) : 0;
  }
  return typeof t.seconds === "number" ? Math.max(0, t.seconds) : null;
}

export function Ticker({ ticker }: TickerProps) {
  const running = Boolean(ticker?.last_capture_at && !ticker?.paid_at);
  const [now, setNow] = useState(() => Date.now());
  const sinceRef = useRef<{ key: string | null; at: number | null }>({ key: null, at: null });

  // Remember when this browser first saw the current capture timestamp.
  const key = ticker?.last_capture_at ?? null;
  if (sinceRef.current.key !== key) {
    sinceRef.current = { key, at: key ? Date.now() : null };
  }

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [running]);

  const seconds = tickerSeconds(ticker, now, sinceRef.current.at);
  const done = Boolean(ticker?.paid_at);
  const mode = done ? "tk-ticker-done" : running ? "tk-ticker-live" : "";
  const sub = done
    ? `paid at ${fmtTime(ticker?.paid_at)}`
    : running
      ? `counting since the capture at ${fmtTime(ticker?.last_capture_at)}`
      : "waiting for the first capture of the round";

  return (
    <section className={`tk-card tk-ticker ${mode}`} aria-label="Payout ticker" aria-live="polite">
      <div className="tk-stat">
        <span className="tk-stat-label">Last contribution to pot paid</span>
        <span className="tk-stat-value">{seconds === null ? "—" : `${seconds.toFixed(1)} s`}</span>
        <span className="tk-stat-sub">{sub}</span>
      </div>
    </section>
  );
}
