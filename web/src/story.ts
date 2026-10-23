/**
 * Judge Mode story (docs/CONTRACTS.md "Builder E"): pure helpers behind the single "Next step" button
 * and its step dots. The server decides which step is next and whether it ran; this only presents it.
 */

import type { StoryStep, StoryView } from "./types";

export interface StoryButton {
  /** Button text, e.g. "Next: Run Round 3 (Ravi declines)". */
  label: string;
  disabled: boolean;
  /** Tooltip / reason when disabled. */
  why: string | null;
  running: boolean;
  complete: boolean;
  /** The step a click runs (null when complete or unknown). */
  step: string | null;
  /** 1-based position of the next step, for "Step 4 of 11". */
  position: number | null;
  total: number;
}

export function storyButton(view: StoryView | null, busy: boolean): StoryButton {
  const total = view?.steps.length ?? 0;
  if (!view) {
    return { label: "Loading the story…", disabled: true, why: "Loading", running: false, complete: false, step: null, position: null, total };
  }
  if (view.complete || !view.next) {
    return {
      label: "Story complete",
      disabled: true,
      why: "Every step ran. Restart to walk it again; the finished run is saved as the replay.",
      running: false,
      complete: true,
      step: null,
      position: null,
      total,
    };
  }
  const running = Boolean(view.running);
  const label = view.next.action_label || `Next: ${view.next.label}`;
  return {
    label,
    disabled: busy || running,
    why: running ? "This step is still running; the button frees up when its outcome holds." : busy ? "Another action is in flight" : null,
    running,
    complete: false,
    step: view.next.step,
    position: (view.next.index ?? 0) + 1,
    total,
  };
}

export type DotState = "done" | "running" | "next" | "todo";

export interface StoryDot {
  step: string;
  label: string;
  state: DotState;
  title: string;
}

function dotState(s: StoryStep): DotState {
  return s.status === "done" || s.status === "running" || s.status === "next" ? s.status : "todo";
}

/** One dot per step, with a tooltip that carries the step's summary and how it ran. */
export function storyDots(view: StoryView | null): StoryDot[] {
  return (view?.steps ?? []).map((s, i) => {
    const state = dotState(s);
    const how = storyModeLabel(s.mode);
    const bits = [`${i + 1}. ${s.label}`];
    if (state === "done" && s.summary) bits.push(s.summary);
    if (state === "done" && how) bits.push(`(${how})`);
    if (state === "running") bits.push("running…");
    return { step: s.step, label: s.label, state, title: bits.join("\n") };
  });
}

export function storyProgress(view: StoryView | null): { done: number; total: number } {
  const steps = view?.steps ?? [];
  return { done: steps.filter((s) => s.status === "done").length, total: steps.length };
}

/** Honest wording for how a step ran: live sandbox, simulated, rules, AI replay, or a judge action. */
export function storyModeLabel(mode: string | null | undefined): string | null {
  switch (mode) {
    case "live":
      return "live sandbox";
    case "simulated":
      return "simulated, no PayPal call";
    case "rules":
      return "rules mode";
    case "replay":
      return "AI replay";
    case "engine":
      return "engine";
    case "judge":
      return "judge action";
    case null:
    case undefined:
    case "":
      return null;
    default:
      return mode;
  }
}

/** SSE `story` start marker: show the step as running before the POST answers. */
export function markStoryStart(view: StoryView | null, step: string): StoryView | null {
  if (!view || step === "reset") return view;
  const idx = view.steps.findIndex((s) => s.step === step);
  if (idx < 0 || view.steps[idx].status === "done") return view;
  const steps = view.steps.map((s, i) => (i === idx ? { ...s, status: "running" } : s));
  return { ...view, steps };
}
