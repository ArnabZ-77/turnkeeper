import type { CircleInfo } from "../types";
import { fmtDateTime } from "../format";
import { PAYPAL_NOT_CONFIGURED, SIMULATE_DECLINE_LABEL, canChargeLive, type RecoveryButtons } from "../recovery";
import { IconBolt, IconCheck, IconClock, IconCoins } from "./icons";

export interface JudgeBarProps {
  circle: CircleInfo | null;
  busy: boolean;
  /** Session flag: PayPal sandbox credentials are configured (else collect/payout answer 503). */
  paypalLive: boolean;
  /** Session flag: every member has a real sandbox vault id. False -> the simulated decline replaces Run Round 3. */
  vaultSeeded: boolean;
  /** Round the collect/payout helpers target (lowest round not yet paid). */
  nextRound: number | null;
  /** Why each recovery step is disabled (null = enabled); see recovery.ts `recoveryButtons`. */
  recovery: RecoveryButtons;
  onReset: () => void;
  onAdvance: (days: number) => void;
  onRunRound: (roundN: number) => void;
  /** POST /api/judge/simulate_round3_decline (offered when the sandbox cannot charge). */
  onSimulateDecline: () => void;
  onToggleRavi: (on: boolean) => void;
  onUnclaimed: (on: boolean) => void;
  onCollect: (roundN: number) => void;
  onPayout: (roundN: number) => void;
  onReplyAsRavi: () => void;
  onStartRecovery: () => void;
  onConsent: () => void;
  onVote: () => void;
  onApprove: () => void;
  onMarkPaid: () => void;
  /** M3: force the T-3 forecast for the next open round. */
  onRunForecast?: () => void;
  /** D1: the round whose current payout item is UNCLAIMED (null hides Re-route). */
  rerouteRound?: number | null;
  onReroute?: (roundN: number) => void;
  /** Builder B: the $5 two-member judge circle that uses the real join flow. */
  onJudgeCircle?: () => void;
  /** Drawer open state (remembered by the App). */
  open?: boolean;
  onToggleOpen?: (open: boolean) => void;
}

export const DEMO_ROUND = 3;
export const ADVANCE_DAYS = 3;

function Step({ n }: { n: number }) {
  return (
    <span className="tk-step" aria-hidden="true">
      {n}
    </span>
  );
}

/**
 * Judge Mode controls, in the order the demo story flows: set up the decline, run the round,
 * then walk the recovery (reply -> plan -> consent -> vote -> approve -> repay). Every button maps
 * to one `/api/judge/*` action or a circle route; the server re-checks everything.
 */
export function JudgeBar(p: JudgeBarProps) {
  const { circle, busy, paypalLive, nextRound, recovery } = p;
  const disabled = busy || !circle;
  const ravi = Boolean(circle?.flags?.ravi_declines);
  const unclaimed = Boolean(circle?.flags?.next_payout_unclaimed);
  const paypalTip = paypalLive ? null : PAYPAL_NOT_CONFIGURED;
  const tip = (why: string | null, ok: string) => why ?? ok;
  return (
    <details
      className="tk-card tk-advanced"
      open={p.open}
      onToggle={(e) => p.onToggleOpen?.((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="tk-advanced-summary">
        <span className="tk-judge-label">Advanced controls</span>
        <span className="tk-muted tk-small">individual judge actions, outside the story order</span>
      </summary>
    <section className="tk-judge-bar" aria-label="Judge mode advanced controls">
      <div className="tk-judge-row">
        <span className="tk-judge-label">Setup</span>
        <button type="button" className="tk-btn" disabled={disabled} onClick={p.onReset} title="New run id, fresh clone of the fixture circle">
          Reset
        </button>
        <button
          type="button"
          className="tk-btn tk-btn-danger"
          aria-pressed={ravi}
          disabled={disabled}
          onClick={() => p.onToggleRavi(!ravi)}
          title="Inject INSTRUMENT_DECLINED on Ravi's next charge (PayPal-Mock-Response header)"
        >
          <Step n={1} /> Ravi declines: {ravi ? "on" : "off"}
        </button>
        {canChargeLive({ paypal_live: paypalLive, vault_seeded: p.vaultSeeded }) ? (
          <button
            type="button"
            className="tk-btn tk-btn-primary"
            disabled={disabled}
            onClick={() => p.onRunRound(DEMO_ROUND)}
            title="Collect round 3: five vaulted charges plus the Ravi scenario"
          >
            <Step n={2} /> <IconBolt /> Run Round {DEMO_ROUND}
          </button>
        ) : (
          <button
            type="button"
            className="tk-btn tk-btn-primary"
            disabled={disabled}
            onClick={p.onSimulateDecline}
            title={`${paypalLive ? "Members not vaulted (TK_SEED_VAULT_IDS)" : PAYPAL_NOT_CONFIGURED}: settles round 3 with labelled REPLAY- captures and Ravi's charge declined (simulated, no PayPal call), so the recovery path can be walked`}
          >
            <Step n={2} /> <IconBolt /> {SIMULATE_DECLINE_LABEL}
          </button>
        )}
        <span className="tk-judge-sep" aria-hidden="true" />
        <span className="tk-judge-label">Recovery</span>
        <button
          type="button"
          className="tk-btn"
          disabled={disabled}
          onClick={p.onReplyAsRavi}
          title="Ravi answers in Hindi: “शुक्रवार को सैलरी आएगी, तब भर दूंगा” (Salary comes on Friday, I will pay then)"
        >
          <Step n={3} /> Reply as Ravi
        </button>
        <button
          type="button"
          className="tk-btn"
          disabled={disabled || recovery.start !== null}
          onClick={p.onStartRecovery}
          title={tip(recovery.start, "The treasurer reads the reply, records the commitment and proposes charter-legal remedies")}
        >
          <Step n={4} /> Start recovery
        </button>
        <button
          type="button"
          className="tk-btn"
          disabled={disabled || recovery.consent !== null}
          onClick={p.onConsent}
          title={tip(recovery.consent, "Ravi and his swap partner consent to the turn swap")}
        >
          <Step n={5} /> Consent (affected)
        </button>
        <button
          type="button"
          className="tk-btn"
          disabled={disabled || recovery.vote !== null}
          onClick={p.onVote}
          title={tip(recovery.vote, "Members vote yes until the charter's quorum is reached")}
        >
          <Step n={6} /> Vote as members
        </button>
        <button
          type="button"
          className="tk-btn tk-btn-primary"
          disabled={disabled || recovery.approve !== null}
          onClick={p.onApprove}
          title={tip(recovery.approve, "Priya's one tap: execution runs through the policy engine (409 lists what is missing)")}
        >
          <Step n={7} /> <IconCheck /> Approve as Priya
        </button>
        <button
          type="button"
          className="tk-btn"
          disabled={disabled || recovery.markPaid !== null}
          onClick={p.onMarkPaid}
          title={tip(recovery.markPaid, "Simulates INVOICING.INVOICE.PAID for Ravi's invoice (labelled simulated)")}
        >
          <Step n={8} /> Mark invoice paid (simulated)
        </button>
      </div>
      <div className="tk-judge-row tk-judge-row-2">
        <button type="button" className="tk-btn tk-btn-sm" disabled={disabled} onClick={() => p.onAdvance(ADVANCE_DAYS)} title="Advance the virtual clock">
          <IconClock /> Advance {ADVANCE_DAYS} days
        </button>
        <button
          type="button"
          className="tk-btn tk-btn-sm tk-btn-warn"
          aria-pressed={unclaimed}
          disabled={disabled}
          onClick={() => p.onUnclaimed(!unclaimed)}
          title="Route the next payout to an unregistered receiver so it comes back UNCLAIMED"
        >
          Next payout unclaimed: {unclaimed ? "on" : "off"}
        </button>
        {nextRound !== null && (
          <>
            <button
              type="button"
              className="tk-btn tk-btn-sm"
              disabled={disabled || !paypalLive}
              onClick={() => p.onCollect(nextRound)}
              title={tip(paypalTip, "POST /rounds/{n}/collect")}
            >
              Collect round {nextRound}
            </button>
            <button
              type="button"
              className="tk-btn tk-btn-sm"
              disabled={disabled || !paypalLive}
              onClick={() => p.onPayout(nextRound)}
              title={tip(paypalTip, "POST /rounds/{n}/payout")}
            >
              <IconCoins /> Pay round {nextRound}
            </button>
          </>
        )}
        {p.onRunForecast && (
          <button
            type="button"
            className="tk-btn tk-btn-sm"
            disabled={disabled}
            onClick={p.onRunForecast}
            title="Re-run the T-3 shortfall forecast for the next open round (rules score; the wording may use one model call)"
          >
            Run forecast
          </button>
        )}
        {p.onReroute && (
          <button
            type="button"
            className="tk-btn tk-btn-sm"
            disabled={disabled || p.rerouteRound == null}
            onClick={() => p.rerouteRound != null && p.onReroute?.(p.rerouteRound)}
            title={
              p.rerouteRound == null
                ? "No payout is UNCLAIMED"
                : `Round ${p.rerouteRound}: wait for the batch, one cancel attempt, then a new batch to the member's other receiver`
            }
          >
            Re-route unclaimed{p.rerouteRound != null ? ` (round ${p.rerouteRound})` : ""}
          </button>
        )}
        {p.onJudgeCircle && (
          <button
            type="button"
            className="tk-btn tk-btn-sm"
            disabled={busy}
            onClick={p.onJudgeCircle}
            title="Creates this session's $5, 2-member circle and opens judge.member's invite: a real PayPal sandbox approval (log in with the published judge.member sandbox account)"
          >
            Try a real PayPal approval
          </button>
        )}
        <span className="tk-spacer" />
        <span className="tk-judge-clock" title="Virtual clock of this circle">
          clock: {fmtDateTime(circle?.virtual_now)}
        </span>
      </div>
    </section>
    </details>
  );
}
