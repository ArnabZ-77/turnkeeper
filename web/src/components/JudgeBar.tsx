import type { CircleInfo } from "../types";
import { fmtDateTime } from "../format";
import { IconBolt, IconClock, IconCoins } from "./icons";

export interface JudgeBarProps {
  circle: CircleInfo | null;
  busy: boolean;
  /** Round the collect/payout helpers target (lowest round not yet paid). */
  nextRound: number | null;
  onReset: () => void;
  onAdvance: (days: number) => void;
  onRunRound: (roundN: number) => void;
  onToggleRavi: (on: boolean) => void;
  onUnclaimed: (on: boolean) => void;
  onCollect: (roundN: number) => void;
  onPayout: (roundN: number) => void;
}

export const DEMO_ROUND = 3;
export const ADVANCE_DAYS = 3;

/** Judge Mode controls: every button maps to one `/api/judge/*` action or a round route. */
export function JudgeBar({
  circle,
  busy,
  nextRound,
  onReset,
  onAdvance,
  onRunRound,
  onToggleRavi,
  onUnclaimed,
  onCollect,
  onPayout,
}: JudgeBarProps) {
  const disabled = busy || !circle;
  const ravi = Boolean(circle?.flags?.ravi_declines);
  const unclaimed = Boolean(circle?.flags?.next_payout_unclaimed);
  return (
    <section className="tk-card tk-judge-bar" aria-label="Judge mode">
      <span className="tk-judge-label">Judge mode</span>
      <button type="button" className="tk-btn" disabled={disabled} onClick={onReset} title="New run id, fresh clone of the fixture circle">
        Reset
      </button>
      <button type="button" className="tk-btn" disabled={disabled} onClick={() => onAdvance(ADVANCE_DAYS)} title="Advance the virtual clock">
        <IconClock /> Advance {ADVANCE_DAYS} days
      </button>
      <button
        type="button"
        className="tk-btn tk-btn-primary"
        disabled={disabled}
        onClick={() => onRunRound(DEMO_ROUND)}
        title="Collect round 3: five vaulted charges plus the Ravi scenario"
      >
        <IconBolt /> Run Round {DEMO_ROUND}
      </button>
      <button
        type="button"
        className="tk-btn tk-btn-danger"
        aria-pressed={ravi}
        disabled={disabled}
        onClick={() => onToggleRavi(!ravi)}
        title="Inject INSTRUMENT_DECLINED on Ravi's next charge (PayPal-Mock-Response header)"
      >
        Ravi declines: {ravi ? "on" : "off"}
      </button>
      <button
        type="button"
        className="tk-btn tk-btn-warn"
        aria-pressed={unclaimed}
        disabled={disabled}
        onClick={() => onUnclaimed(!unclaimed)}
        title="Route the next payout to an unregistered receiver so it comes back UNCLAIMED"
      >
        Next payout unclaimed: {unclaimed ? "on" : "off"}
      </button>
      <span className="tk-spacer" />
      {nextRound !== null && (
        <>
          <button type="button" className="tk-btn tk-btn-sm" disabled={disabled} onClick={() => onCollect(nextRound)} title="POST /rounds/{n}/collect">
            Collect round {nextRound}
          </button>
          <button type="button" className="tk-btn tk-btn-sm" disabled={disabled} onClick={() => onPayout(nextRound)} title="POST /rounds/{n}/payout">
            <IconCoins /> Pay round {nextRound}
          </button>
        </>
      )}
      <span className="tk-judge-clock" title="Virtual clock of this circle">
        clock: {fmtDateTime(circle?.virtual_now)}
      </span>
    </section>
  );
}
