import type { StoryView } from "../types";
import { storyButton, storyDots, storyModeLabel, storyProgress } from "../story";
import { IconBolt, IconCheck } from "./icons";

export interface StoryBarProps {
  story: StoryView | null;
  busy: boolean;
  onNext: (step: string) => void;
  onRestart: () => void;
}

/**
 * Judge Mode's main control: one prominent "Next step" button that reads the server's next action label,
 * with one dot per story step. The individual buttons live in the "Advanced controls" drawer below.
 */
export function StoryBar({ story, busy, onNext, onRestart }: StoryBarProps) {
  const btn = storyButton(story, busy);
  const dots = storyDots(story);
  const { done, total } = storyProgress(story);
  const simulated = story?.paypal === "simulated";
  const last = [...(story?.steps ?? [])].reverse().find((s) => s.status === "done");
  const lastHow = storyModeLabel(last?.mode);
  return (
    <section className="tk-card tk-story" aria-label="Judge mode story">
      <div className="tk-story-head">
        <span className="tk-judge-label">Judge mode</span>
        <span className="tk-story-count" aria-live="polite">
          {total ? `${done} / ${total} steps` : "—"}
        </span>
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
              {d.state === "done" ? <IconCheck /> : i + 1}
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
          className={`tk-btn tk-btn-primary tk-btn-hero${btn.running ? " tk-btn-running" : ""}`}
          disabled={btn.disabled || !btn.step}
          onClick={() => btn.step && onNext(btn.step)}
          title={btn.why ?? (btn.position ? `Step ${btn.position} of ${btn.total}` : undefined)}
        >
          {btn.complete ? <IconCheck /> : <IconBolt />}
          <span className="tk-btn-hero-text">{btn.label}</span>
        </button>
        <button type="button" className="tk-btn" disabled={busy || Boolean(story?.running)} onClick={onRestart} title="Reset the story: new circle, new run id">
          Restart
        </button>
        {story?.complete && story.replay_url && (
          <a className="tk-btn tk-btn-ghost" href={story.replay_url} target="_blank" rel="noreferrer" title="The finished run, saved as the canonical recording">
            Recorded replay (JSON)
          </a>
        )}
      </div>
      <p className="tk-story-last tk-small tk-muted" aria-live="polite">
        {last ? (
          <>
            Last: {last.summary ?? last.label}
            {lastHow ? ` · ${lastHow}` : ""}
          </>
        ) : (
          "Each click runs one step of the demo; the server checks every step's outcome before moving on."
        )}
      </p>
    </section>
  );
}
