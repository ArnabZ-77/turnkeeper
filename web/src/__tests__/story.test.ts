import { describe, expect, it } from "vitest";
import { markStoryStart, storyButton, storyDots, storyModeLabel, storyProgress } from "../story";
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
    expect(storyModeLabel("live")).toBe("live sandbox");
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
