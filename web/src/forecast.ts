/**
 * T-3 shortfall forecast card (docs/CONTRACTS.md "M3 builder C"): pure mappers from
 * `BoardModel.forecasts` and the `forecast` SSE event. A flag is a nudge, not a verdict: the card never
 * claims the forecast caused (or will cause) a decline, and it cites the backtest instead (PLAN N6).
 */

import type { BoardModel, ForecastEvent, ForecastInfo } from "./types";

export interface ForecastModeChip {
  cls: string;
  label: string;
  tip: string;
}

/** The forecaster's own mode: the score is always deterministic; only the wording may come from a model. */
export function forecastModeChip(mode: string | null | undefined): ForecastModeChip {
  switch (mode) {
    case "live":
      return { cls: "tk-chip-accent", label: "live AI wording", tip: "Score from fixed rules; the reason and nudge were written by the model just now." };
    case "replay":
      return { cls: "tk-chip-replay", label: "AI replay wording", tip: "Score from fixed rules; the wording is a recorded model answer (no model call)." };
    default:
      return { cls: "tk-chip-pending", label: "template", tip: "Score from fixed rules; the reason and nudge are en/hi templates (no model)." };
  }
}

export const BACKTEST_NOTE =
  "Backtest on 60 seeded member-rounds: precision 0.65, recall 0.65. A flag means a friendly nudge, nothing more.";

export interface ForecastCardView {
  roundN: number;
  flagged: ForecastInfo[];
  /** Members scored and not flagged in the same round. */
  clear: number;
  asOf: string | null;
  /** The card's chip: the mode of the flagged rows (mixed -> the most "live" one). */
  chip: ForecastModeChip;
}

const MODE_RANK: Record<string, number> = { live: 3, replay: 2, rules: 1 };

/** The latest forecast round on the board, flagged members first by score. Null before any forecast. */
export function forecastCard(board: BoardModel | null): ForecastCardView | null {
  const rows = board?.forecasts ?? [];
  if (!rows.length) return null;
  const roundN = Math.max(...rows.map((r) => r.round_n));
  const inRound = rows.filter((r) => r.round_n === roundN);
  const flagged = inRound.filter((r) => r.flagged).sort((a, b) => b.score - a.score || a.member.localeCompare(b.member));
  const mode = flagged.map((r) => r.mode ?? "rules").sort((a, b) => (MODE_RANK[b] ?? 0) - (MODE_RANK[a] ?? 0))[0] ?? "rules";
  return {
    roundN,
    flagged,
    clear: inRound.length - flagged.length,
    asOf: inRound.find((r) => r.as_of)?.as_of ?? null,
    chip: forecastModeChip(mode),
  };
}

/** "85%" for a 0..1 score. */
export function scorePct(score: number | null | undefined): string {
  const n = Number(score);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(Math.max(0, Math.min(1, n)) * 100)}%`;
}

/** Fold a `forecast` event (one per flagged member) into the board so the card updates before the refetch. */
export function applyForecastEvent(board: BoardModel, ev: ForecastEvent): BoardModel {
  const rows = [...(board.forecasts ?? [])];
  const idx = rows.findIndex((r) => r.round_n === ev.round_n && r.member === ev.member);
  const prev = idx >= 0 ? rows[idx] : null;
  const next: ForecastInfo = {
    round_n: ev.round_n,
    member: ev.member,
    score: Number(ev.score) || 0,
    flagged: true,
    reason: ev.reason ?? prev?.reason ?? null,
    nudge: ev.nudge ?? prev?.nudge ?? null,
    nudge_en: ev.nudge_en ?? prev?.nudge_en ?? null,
    lang: ev.lang ?? prev?.lang ?? null,
    mode: ev.mode ?? prev?.mode ?? null,
    factors: prev?.factors,
    as_of: prev?.as_of ?? null,
  };
  if (idx >= 0) rows[idx] = next;
  else rows.unshift(next);
  return { ...board, forecasts: rows };
}
