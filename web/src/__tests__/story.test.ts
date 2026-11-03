import { describe, expect, it } from "vitest";
import {
  failureHelp,
  failureText,
  lastStoryAi,
  markStoryStart,
  parseStepFailureToast,
  storyButton,
  storyDots,
  storyFailure,
  storyHow,
  storyHowLabel,
  storyModeLabel,
  storyProgress,
} from "../story";
import { initialState, reducer } from "../state";
import type { StoryView } from "../types";

const NAMES = ["reset", "advance_to_t3", "run_round3", "reply_as_ravi"];

function view(doneCount: number, opts: Partial<StoryView> = {}): StoryView {
  const steps = NAMES.map((step, i) => ({
    step,
    label: `Label ${step}`,
    status: i < doneCount ? "done" : i === doneCount ? "next" : "todo",
    at: i < doneCount ? "2026-10-23T10:00:00Z" : null,
    mode: i < doneCount ? (i === 2 ? "simulated" : "judge") : null,
    summary: i < doneCount ? `did ${step}` : null,
  }));
  const nxt = doneCount < NAMES.length ? NAMES[doneCount] : null;
  return {
    steps,
    current: doneCount ? NAMES[doneCount - 1] : null,
    next: nxt ? { step: nxt, label: `Label ${nxt}`, action_label: `Next: Label ${nxt}`, index: doneCount } : null,
    running: null,
    complete: nxt === null,
    paypal: "simulated",
    replay_url: "/api/replay",
    ...opts,
  };
}

describe("storyButton", () => {
  it("reads the server's action label and targets the next step", () => {
    const b = storyButton(view(2), false);
    expect(b.label).toBe("Next: Label run_round3");
    expect(b.step).toBe("run_round3");
    expect(b.position).toBe(3);
    expect(b.total).toBe(4);
    expect(b.disabled).toBe(false);
  });

  it("is disabled while busy or while a background step runs", () => {
    expect(storyButton(view(1), true).disabled).toBe(true);
    const running = storyButton(view(1, { running: { step: "advance_to_t3", task_id: "t1" } }), false);
    expect(running.disabled).toBe(true);
    expect(running.running).toBe(true);
    expect(running.why).toMatch(/still running/);
  });

  it("reports completion and loading honestly", () => {
    const done = storyButton(view(4), false);
    expect(done.complete).toBe(true);
    expect(done.step).toBeNull();
    expect(done.disabled).toBe(true);
    expect(storyButton(null, false).label).toMatch(/Loading/);
  });
});

describe("storyDots / progress / mode", () => {
  it("maps statuses to dot states with summary tooltips", () => {
    const dots = storyDots(view(3));
    expect(dots.map((d) => d.state)).toEqual(["done", "done", "done", "next"]);
    expect(dots[2].title).toContain("did run_round3");
    expect(dots[2].title).toContain("simulated, no PayPal call");
    expect(storyProgress(view(3))).toEqual({ done: 3, total: 4 });
  });

  it("labels modes honestly and passes unknown ones through", () => {
    // A bare "live" is ambiguous (AI or PayPal): never rendered as a PayPal claim.
    expect(storyModeLabel("live")).toBe("live");
    expect(storyModeLabel("rules")).toBe("rules mode");
    expect(storyModeLabel(null)).toBeNull();
    expect(storyModeLabel("weird")).toBe("weird");
  });
});

describe("story in the reducer", () => {
  it("stores the view and marks a started step as running from the SSE marker", () => {
    let s = reducer(initialState, { type: "story", story: view(2) });
    s = reducer(s, { type: "event", event: { type: "story", ts: "t", phase: "start", step: "run_round3", index: 2 } });
    expect(s.story!.steps[2].status).toBe("running");
    // reset markers and unknown steps leave the view untouched
    const same = reducer(s, { type: "event", event: { type: "story", ts: "t", phase: "start", step: "nope" } });
    expect(same.story).toBe(s.story);
    expect(markStoryStart(view(2), "reset")!.steps[0].status).toBe("done");
  });
});

describe("storyHow: PayPal and AI are separate facts", () => {
  const step = (name: string, mode: string | null, extra: object = {}) => ({
    step: name,
    label: name,
    status: "done",
    at: null,
    mode,
    summary: null,
    ...extra,
  });

  it("never shows an AI 'live' forecast as a live PayPal call", () => {
    const how = storyHow(step("advance_to_t3", "live"), { paypal: "live" });
    expect(how).toEqual({ paypal: null, ai: "live", judge: false });
    expect(storyHowLabel(step("advance_to_t3", "live"), { paypal: "live" })).toBe("AI: live");
  });

  it("a live-sandbox round run by the agent in rules mode says both", () => {
    expect(storyHowLabel(step("run_round3", "rules"), { paypal: "live" })).toBe("PayPal sandbox live · rules mode");
    expect(storyHowLabel(step("run_round3", "simulated"), { paypal: "simulated" })).toBe("simulated, no PayPal call");
  });

  it("execute follows the invoice, and newer servers' explicit fields win", () => {
    expect(storyHowLabel(step("execute", "rules"), null, { invoiceSimulated: true })).toBe("simulated, no PayPal call · rules mode");
    expect(storyHowLabel(step("execute", "rules"), null, { invoiceSimulated: false })).toBe("PayPal sandbox live · rules mode");
    expect(storyHowLabel(step("payout", "x", { paypal: "live", ai: "replay" }), null)).toBe("PayPal sandbox live · AI: replay");
    expect(storyHowLabel(step("reset", "judge"), null)).toBe("judge action");
  });

  it("lastStoryAi reads the latest finished step that used the AI", () => {
    const v = view(3);
    expect(lastStoryAi(v)).toBeNull(); // judge + simulated steps only
    v.steps[1] = { ...v.steps[1], mode: "rules" };
    expect(lastStoryAi(v)).toBe("rules");
    expect(lastStoryAi(null)).toBeNull();
  });
});

describe("story failures", () => {
  it("keeps a failed step visible, offers a retry and marks the dot", () => {
    const v = view(2);
    const failure = { step: "run_round3", text: "PayPal 422 INSUFFICIENT_FUNDS" };
    const b = storyButton(v, false, failure);
    expect(b.retry).toBe(true);
    expect(b.label).toBe("Retry: Label run_round3");
    expect(storyDots(v, failure)[2].state).toBe("failed");
    expect(failureText(v, failure)).toBe(
      "Label run_round3 failed: PayPal sandbox balance too low (INSUFFICIENT_FUNDS). Nothing was paid.",
    );
  });

  it("explains an INSUFFICIENT_FUNDS payout and points at the replay, without claiming money moved", () => {
    const v = view(9);
    const help = failureHelp(v, { step: "pay_round3", text: "PayPal 422 INSUFFICIENT_FUNDS for the 1154.93 pot" });
    expect(help?.text).toContain("No money moved");
    expect(help?.text).not.toContain("was paid");
    expect(help?.replayUrl).toBe(v.replay_url || "/api/replay");
    expect(failureHelp({ ...v, replay_url: null }, { step: "pay_round3", text: "INSUFFICIENT_FUNDS" })?.replayUrl).toBe("/api/replay");
    expect(failureHelp(v, { step: "pay_round3", text: "boom" })).toBeNull();
  });

  it("drops the failure once the step runs or moves on, and prefers the server's error", () => {
    const failure = { step: "run_round3", text: "boom" };
    expect(storyFailure(view(2), failure)).toEqual(failure);
    expect(storyFailure(view(2, { running: { step: "run_round3", task_id: "t" } }), failure)).toBeNull();
    expect(storyFailure(view(3), failure)).toBeNull();
    const v = view(2);
    v.steps[2] = { ...v.steps[2], error: "server says no" };
    expect(storyFailure(v, null)).toEqual({ step: "run_round3", text: "server says no" });
  });

  it("parses the background-step failure toast and keeps it in the reducer until a reset", () => {
    expect(parseStepFailureToast("Story step 'payout' did not finish: 409 x")).toEqual({ step: "payout", text: "409 x" });
    expect(parseStepFailureToast("hello")).toBeNull();
    let s = reducer(initialState, { type: "story", story: view(2) });
    s = reducer(s, { type: "storyError", step: "run_round3", text: "x" });
    s = reducer(s, { type: "story", story: view(2) });
    expect(s.storyFailure).toEqual({ step: "run_round3", text: "x" });
    s = reducer(s, { type: "story", story: view(3) });
    expect(s.storyFailure).toBeNull();
  });
});
