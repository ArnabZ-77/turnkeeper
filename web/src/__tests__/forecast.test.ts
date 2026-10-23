import { describe, expect, it } from "vitest";
import { demoBoard } from "./fixtures";
import { applyForecastEvent, forecastCard, forecastModeChip, scorePct } from "../forecast";
import { initialState, reducer } from "../state";
import type { ForecastInfo } from "../types";

function row(member: string, score: number, flagged: boolean, extra: Partial<ForecastInfo> = {}): ForecastInfo {
  return {
    round_n: 3,
    member,
    score,
    flagged,
    reason: flagged ? "Payday falls after the due date" : null,
    nudge: flagged ? "नमस्ते {name}" : null,
    nudge_en: flagged ? "Hi" : null,
    lang: "hi",
    mode: "rules",
    factors: [],
    as_of: "2026-12-29T09:00:00Z",
    ...extra,
  };
}

describe("forecastCard", () => {
  it("is null before any forecast", () => {
    expect(forecastCard(demoBoard())).toBeNull();
    expect(forecastCard(null)).toBeNull();
  });

  it("shows the latest round, flagged first by score, with the clear count", () => {
    const board = demoBoard({
      forecasts: [row("meena", 0.05, false), row("ravi", 0.85, true), row("asha", 0.6, true), row("ravi", 0.9, true, { round_n: 2 })],
    });
    const v = forecastCard(board)!;
    expect(v.roundN).toBe(3);
    expect(v.flagged.map((f) => f.member)).toEqual(["ravi", "asha"]);
    expect(v.clear).toBe(1);
    expect(v.chip.label).toBe("template");
    expect(v.asOf).toBe("2026-12-29T09:00:00Z");
  });

  it("labels the wording mode: rules is a template, never 'AI'", () => {
    expect(forecastModeChip("rules").label).toBe("template");
    expect(forecastModeChip(undefined).label).toBe("template");
    expect(forecastModeChip("replay").label).toBe("AI replay wording");
    expect(forecastModeChip("live").label).toBe("live AI wording");
    const mixed = forecastCard(demoBoard({ forecasts: [row("ravi", 0.85, true), row("asha", 0.6, true, { mode: "replay" })] }))!;
    expect(mixed.chip.label).toBe("AI replay wording");
  });

  it("formats scores as clamped percentages", () => {
    expect(scorePct(0.85)).toBe("85%");
    expect(scorePct(1.4)).toBe("100%");
    expect(scorePct(Number.NaN)).toBe("—");
  });
});

describe("forecast events", () => {
  it("upserts a flagged row and the reducer applies it to the board", () => {
    const ev = {
      type: "forecast" as const,
      ts: "t",
      round_n: 3,
      member: "ravi",
      score: 0.85,
      reason: "r",
      nudge: "n",
      nudge_en: "e",
      lang: "hi",
      mode: "replay",
    };
    const b1 = applyForecastEvent(demoBoard(), ev);
    expect(b1.forecasts).toHaveLength(1);
    expect(b1.forecasts![0].flagged).toBe(true);
    const b2 = applyForecastEvent(b1, { ...ev, score: 0.9 });
    expect(b2.forecasts).toHaveLength(1);
    expect(b2.forecasts![0].score).toBe(0.9);

    let s = reducer(initialState, { type: "board", board: demoBoard() });
    s = reducer(s, { type: "event", event: ev });
    expect(forecastCard(s.board)!.flagged[0].member).toBe("ravi");
    // no board yet: ignored
    expect(reducer(initialState, { type: "event", event: ev }).board).toBeNull();
  });
});
