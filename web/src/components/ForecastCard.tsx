import type { BoardModel } from "../types";
import { BACKTEST_NOTE, forecastCard, scorePct } from "../forecast";
import { nameOf } from "../recovery";
import { fmtDateTime } from "../format";

export interface ForecastCardProps {
  board: BoardModel | null;
  /** The member's phone is on screen right below: show the nudge's English only, not the same Hindi twice. */
  phoneShown?: boolean;
}

/**
 * T-3 shortfall forecast: who is likely to be short this round, why, and the nudge the treasurer sent.
 * Always rendered (with a placeholder) so the rail does not jump when the forecast arrives.
 */
export function ForecastCard({ board, phoneShown = false }: ForecastCardProps) {
  const view = forecastCard(board);
  return (
    <section className="tk-card tk-forecast" aria-label="Shortfall forecast">
      <h2 className="tk-card-title">
        <span>Forecast{view ? ` · round ${view.roundN} (T-3)` : ""}</span>
        {view && (
          <span className={`tk-chip ${view.chip.cls}`} title={view.chip.tip}>
            <span className="tk-dot" />
            {view.chip.label}
          </span>
        )}
      </h2>
      {!view && <p className="tk-muted tk-small tk-forecast-empty">Runs three days before each due date (advance the clock to T-3).</p>}
      {view && view.flagged.length === 0 && (
        <p className="tk-small">Nobody flagged for round {view.roundN}: {view.clear} members scored low risk.</p>
      )}
      {view?.flagged.map((f) => (
        <div className="tk-forecast-row tk-enter" key={`${f.round_n}-${f.member}`}>
          <div className="tk-forecast-top">
            <strong>{nameOf(board, f.member)}</strong>
            <span className="tk-forecast-score" title="Deterministic rules score (late history, payday vs due date, replies, failed attempts, track record)">
              {scorePct(f.score)} risk
            </span>
            <span className="tk-forecast-meter" aria-hidden="true">
              <span style={{ width: scorePct(f.score) }} />
            </span>
          </div>
          {f.reason && <p className="tk-forecast-reason">{f.reason}</p>}
          {f.nudge && phoneShown && f.nudge_en && f.nudge_en !== f.nudge ? (
            <blockquote className="tk-forecast-nudge" lang="en">
              <span className="tk-small tk-muted">Nudge sent to the inbox (Hindi, on the phone below)</span>
              <span className="tk-forecast-en">{f.nudge_en}</span>
            </blockquote>
          ) : f.nudge ? (
            <blockquote className="tk-forecast-nudge" lang={f.lang ?? undefined}>
              <span className="tk-small tk-muted">Nudge sent to the inbox</span>
              <span>{f.nudge}</span>
              {f.nudge_en && f.nudge_en !== f.nudge && <span className="tk-forecast-en">{f.nudge_en}</span>}
            </blockquote>
          ) : null}
        </div>
      ))}
      {view && (
        <p className="tk-small tk-muted tk-forecast-note">
          {view.flagged.length > 0 && view.clear > 0 ? `${view.clear} others scored low. ` : ""}
          {BACKTEST_NOTE}
          {view.asOf ? ` As of ${fmtDateTime(view.asOf)} (virtual clock).` : ""}
        </p>
      )}
    </section>
  );
}
