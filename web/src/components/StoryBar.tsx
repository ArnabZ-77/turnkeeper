import type { StoryView } from "../types";
import { failureText, storyButton, storyDots, storyHowLabel, storyProgress, type StoryFailure } from "../story";
import { IconBolt, IconCheck, IconX } from "./icons";

export interface StoryBarProps {
  story: StoryView | null;
  busy: boolean;
  /** The next step's last failed attempt (already filtered by story.ts storyFailure). */
  failure: StoryFailure | null;
  /** The recovery invoice was simulated (true) or sent through PayPal (false); undefined when unknown. */
  invoiceSimulated?: boolean;
  onNext: (step: string) => void;
  onRestart: () => void;
  /** Reply as Ravi with the held-out eval s06 text (a planted amount instruction) instead of the default. */
  onInjection?: () => void;
}

export const INJECTION_LABEL = "Injection test (eval s06)";
export const INJECTION_TITLE =
  "Ravi replies with the held-out eval s06 text: a payday plus a planted instruction to set the invoice to 5000.00. " +
  "The reply is treated as data; this run is never saved as the public replay.";

export const RESTART_WHILE_RUNNING =
  "A step is still running on the old circle. Restart anyway? The new circle starts clean; the old run is detached.";

/**
 * Judge Mode's main control: one prominent "Next step" button that reads the server's next action label,
 * with one dot per story step. The individual buttons live in the "Advanced controls" drawer below.
 */
export function StoryBar({ story, busy, failure, invoiceSimulated, onNext, onRestart, onInjection }: StoryBarProps) {
  const btn = storyButton(story, busy, failure);
  const dots = storyDots(story, failure);
  const { done, total } = storyProgress(story);
  const simulated = story?.paypal === "simulated";
  const last = [...(story?.steps ?? [])].reverse().find((s) => s.status === "done");
  const how = last ? storyHowLabel(last, story, { invoiceSimulated }) : null;
  // Skip the "how" when the server's summary already says it ("..., simulated, no PayPal call").
  const lastHow = how && !(last?.summary ?? "").includes(how) ? how : null;
  const restart = () => {
    // The server always allows a reset; only ask when it would detach a running step.
    if (story?.running && typeof window !== "undefined" && !window.confirm(RESTART_WHILE_RUNNING)) return;
    onRestart();
  };
  return (
    <section className="tk-card tk-story" aria-label="Judge mode story">
      <div className="tk-story-head">
        <span className="tk-judge-label">Judge mode</span>
        <span className="tk-story-count">{total ? `${done} / ${total} steps` : "—"}</span>
        {story && (
          <span
            className={`tk-chip ${simulated ? "tk-chip-warn" : "tk-chip-good"}`}
            title={
              simulated
                ? "No live sandbox for this circle (credentials or seeded vault ids missing): the round and payout steps are simulated and say so."
                : "Round and payout steps call the PayPal sandbox."
            }
          >
            <span className="tk-dot" />
            {simulated ? "PayPal steps simulated" : "PayPal sandbox live"}
          </span>
        )}
      </div>
      <ol className="tk-story-dots" aria-label="Story steps">
        {dots.map((d, i) => (
          <li key={d.step} className={`tk-story-dot tk-story-${d.state}`} title={d.title}>
            <span className="tk-story-dot-mark" aria-hidden="true">
              {d.state === "done" ? <IconCheck /> : d.state === "failed" ? <IconX /> : i + 1}
            </span>
            <span className="tk-visually-hidden">
              {d.label}: {d.state}
            </span>
          </li>
        ))}
      </ol>
      <div className="tk-story-actions">
        <button
          type="button"
          className={`tk-btn tk-btn-primary tk-btn-hero${btn.running ? " tk-btn-running" : ""}${btn.retry ? " tk-btn-retry" : ""}`}
          disabled={btn.disabled || !btn.step}
          onClick={() => btn.step && onNext(btn.step)}
          title={btn.why ?? (btn.position ? `Step ${btn.position} of ${btn.total}` : undefined)}
        >
          {btn.complete ? <IconCheck /> : <IconBolt />}
          <span className="tk-btn-hero-text">{btn.label}</span>
        </button>
        {onInjection && btn.step === "reply_as_ravi" && !btn.disabled && (
          <button type="button" className="tk-btn tk-btn-ghost" onClick={onInjection} title={INJECTION_TITLE}>
            {INJECTION_LABEL}
          </button>
        )}
        <button
          type="button"
          className="tk-btn"
          disabled={busy}
          onClick={restart}
          title={story?.running ? "Reset the story even though a step is still running (asks first)" : "Reset the story: new circle, new run id"}
        >
          Restart
        </button>
        {story?.complete && story.replay_url && (
          <a className="tk-btn tk-btn-ghost" href={story.replay_url} target="_blank" rel="noreferrer" title="The finished run, saved as the canonical recording">
            Recorded replay (JSON)
          </a>
        )}
      </div>
      {failure && story ? (
        <p className="tk-callout tk-callout-critical tk-story-error" role="alert">
          <IconX /> {failureText(story, failure)}
        </p>
      ) : (
        <p className="tk-story-last tk-small tk-muted">
          {last ? (
            <>
              Last: {last.summary ?? last.label}
              {lastHow ? ` · ${lastHow}` : ""}
            </>
          ) : (
            "Each click runs one step of the demo; the server checks every step's outcome before moving on."
          )}
        </p>
      )}
    </section>
  );
}
