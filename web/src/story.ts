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
  /** The next step failed on its last attempt: the click is a retry. */
  retry: boolean;
}

/** The last failed attempt at a story step (kept until the step is retried, passes, or the story resets). */
export interface StoryFailure {
  step: string;
  text: string;
}

export function storyButton(view: StoryView | null, busy: boolean, failure: StoryFailure | null = null): StoryButton {
  const total = view?.steps.length ?? 0;
  if (!view) {
    return { label: "Loading the story…", disabled: true, why: "Loading", running: false, complete: false, step: null, position: null, total, retry: false };
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
      retry: false,
    };
  }
  const running = Boolean(view.running);
  const retry = !running && failure !== null && failure.step === view.next.step;
  const label = retry ? `Retry: ${view.next.label}` : view.next.action_label || `Next: ${view.next.label}`;
  return {
    label,
    disabled: busy || running,
    why: running ? "This step is still running; the button frees up when its outcome holds." : busy ? "Another action is in flight" : null,
    running,
    complete: false,
    step: view.next.step,
    position: (view.next.index ?? 0) + 1,
    total,
    retry,
  };
}

export type DotState = "done" | "running" | "next" | "todo" | "failed";

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
export function storyDots(view: StoryView | null, failure: StoryFailure | null = null): StoryDot[] {
  return (view?.steps ?? []).map((s, i) => {
    let state = dotState(s);
    if (state === "next" && failure?.step === s.step) state = "failed";
    const how = storyHowLabel(s, view);
    const bits = [`${i + 1}. ${s.label}`];
    if (state === "done" && s.summary) bits.push(s.summary);
    if (state === "done" && how) bits.push(`(${how})`);
    if (state === "running") bits.push("running…");
    if (state === "failed" && failure) bits.push(`failed: ${failure.text}`);
    return { step: s.step, label: s.label, state, title: bits.join("\n") };
  });
}

export function storyProgress(view: StoryView | null): { done: number; total: number } {
  const steps = view?.steps ?? [];
  return { done: steps.filter((s) => s.status === "done").length, total: steps.length };
}

// ---------------------------------------------------------------------------------------------
// How a step ran: PayPal and AI are two separate facts
// ---------------------------------------------------------------------------------------------

export type PayPalHow = "live" | "simulated" | null;
export type AiHow = "live" | "replay" | "rules" | "engine" | null;

export interface StoryHow {
  paypal: PayPalHow;
  ai: AiHow;
  judge: boolean;
}

/** Steps that call PayPal Orders/Payouts (Invoicing for `execute` is read from the recovery invoice). */
const PAYPAL_STEPS = new Set(["run_round3", "payout", "clean_round4"]);
/** Steps whose recorded `mode` is the treasurer agent's (or the forecaster's) AI mode. */
const AI_STEPS = new Set(["advance_to_t3", "run_round3", "start_recovery", "clean_round4"]);

function aiOf(v: string | null | undefined): AiHow {
  return v === "live" || v === "replay" || v === "rules" || v === "engine" ? v : null;
}
function paypalOf(v: string | null | undefined): PayPalHow {
  return v === "live" || v === "simulated" ? v : null;
}

export interface StoryHowContext {
  /** The recovery invoice was simulated (true), sent through PayPal (false), or is unknown (undefined). */
  invoiceSimulated?: boolean;
}

/**
 * Split a step's single legacy `mode` into PayPal and AI facts. Newer servers send `paypal` and `ai`
 * directly; for older ones the step name says what the mode means. An AI "live" is never shown as a
 * live PayPal call, and a background PayPal step run by the agent in rules mode still says PayPal live.
 */
export function storyHow(s: StoryStep, view: Pick<StoryView, "paypal"> | null, ctx: StoryHowContext = {}): StoryHow {
  if (s.paypal !== undefined || s.ai !== undefined) {
    return { paypal: paypalOf(s.paypal), ai: aiOf(s.ai), judge: s.mode === "judge" };
  }
  const mode = s.mode ?? null;
  if (mode === "judge") return { paypal: null, ai: null, judge: true };
  const circlePaypal: PayPalHow = view?.paypal === "live" ? "live" : view?.paypal === "simulated" ? "simulated" : null;
  if (s.step === "payout") {
    // Recorded synchronously: "live" means the Payouts call, "simulated" means skipped.
    return { paypal: paypalOf(mode), ai: null, judge: false };
  }
  if (s.step === "execute") {
    const paypal: PayPalHow =
      ctx.invoiceSimulated === true ? "simulated" : ctx.invoiceSimulated === false ? "live" : null;
    return { paypal, ai: aiOf(mode), judge: false };
  }
  if (PAYPAL_STEPS.has(s.step)) {
    if (mode === "simulated") return { paypal: "simulated", ai: null, judge: false };
    // Background task: the mode is the treasurer's AI mode; PayPal follows the circle.
    return { paypal: circlePaypal, ai: aiOf(mode), judge: false };
  }
  if (AI_STEPS.has(s.step)) return { paypal: null, ai: aiOf(mode), judge: false };
  return { paypal: paypalOf(mode), ai: aiOf(mode), judge: false };
}

const PAYPAL_TEXT: Record<Exclude<PayPalHow, null>, string> = {
  live: "PayPal sandbox live",
  simulated: "simulated, no PayPal call",
};
const AI_TEXT: Record<Exclude<AiHow, null>, string> = {
  live: "AI: live",
  replay: "AI: replay",
  rules: "rules mode",
  engine: "engine",
};

/** "PayPal sandbox live · rules", "AI: live", "judge action", ... (null when nothing is known). */
export function storyHowText(how: StoryHow): string | null {
  if (how.judge) return "judge action";
  const parts = [how.paypal ? PAYPAL_TEXT[how.paypal] : null, how.ai ? AI_TEXT[how.ai] : null].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/**
 * How the AI ran in the latest finished step that used it (for the header chip after a reload, when
 * no trace line or task has reported a mode yet). Null when no finished step says.
 */
export function lastStoryAi(view: StoryView | null): Exclude<AiHow, null> | null {
  const steps = view?.steps ?? [];
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const s = steps[i];
    if (s.status !== "done") continue;
    const ai = storyHow(s, view).ai;
    if (ai) return ai;
  }
  return null;
}

export function storyHowLabel(s: StoryStep, view: Pick<StoryView, "paypal"> | null, ctx: StoryHowContext = {}): string | null {
  return storyHowText(storyHow(s, view, ctx));
}

/**
 * Honest wording for a bare mode string (no step context). "live" is ambiguous on its own, so it is
 * never rendered as a PayPal claim; prefer storyHowLabel when the step is known.
 */
export function storyModeLabel(mode: string | null | undefined): string | null {
  switch (mode) {
    case "live":
      return "live";
    case "simulated":
      return "simulated, no PayPal call";
    case "rules":
      return "rules mode";
    case "replay":
      return "AI: replay";
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

// ---------------------------------------------------------------------------------------------
// Failures
// ---------------------------------------------------------------------------------------------

/** Plain-language reason for a failed step, from the server's error text. */
export function failureReason(raw: string): string {
  const text = raw.trim();
  if (/INSUFFICIENT_FUNDS/i.test(text)) return "PayPal sandbox balance too low (INSUFFICIENT_FUNDS). Nothing was paid.";
  return text || "the server did not say why";
}

/** "Pay Round 3 failed: PayPal sandbox balance too low (INSUFFICIENT_FUNDS). Nothing was paid." */
export function failureText(view: StoryView | null, failure: StoryFailure): string {
  const label = view?.steps.find((s) => s.step === failure.step)?.label ?? failure.step;
  return `${label} failed: ${failureReason(failure.text)}`;
}

/** The SSE toast the server sends when a background step does not hold: "Story step 'x' did not finish: ...". */
const STEP_FAILED = /^Story step '([A-Za-z0-9_]+)' did not finish: ([\s\S]*)$/;

export function parseStepFailureToast(text: string): StoryFailure | null {
  const m = STEP_FAILED.exec(text.trim());
  return m ? { step: m[1], text: m[2] } : null;
}

/** The failure still applies only while its step is the next one and nothing is running. */
export function activeFailure(view: StoryView | null, failure: StoryFailure | null): StoryFailure | null {
  if (!failure || !view || view.running) return null;
  if (view.next?.step === failure.step) return failure;
  // A newer server reports the error on the step itself.
  return null;
}

/** A failure the server keeps on the next step (newer servers), else the client's own record. */
export function storyFailure(view: StoryView | null, local: StoryFailure | null): StoryFailure | null {
  const next = view?.next ? view.steps.find((s) => s.step === view.next?.step) : undefined;
  if (next?.error && !view?.running) return { step: next.step, text: next.error };
  return activeFailure(view, local);
}

/** SSE `story` start marker: show the step as running before the POST answers. */
export function markStoryStart(view: StoryView | null, step: string): StoryView | null {
  if (!view || step === "reset") return view;
  const idx = view.steps.findIndex((s) => s.step === step);
  if (idx < 0 || view.steps[idx].status === "done") return view;
  const steps = view.steps.map((s, i) => (i === idx ? { ...s, status: "running" } : s));
  return { ...view, steps };
}
