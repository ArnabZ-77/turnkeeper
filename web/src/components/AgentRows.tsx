import type { AgentRow } from "../mandates";
import { pendingAsks } from "../mandates";
import { IconCheck, IconClock, IconX } from "./icons";

export interface AgentRowsProps {
  rows: AgentRow[];
  /** Accessible name of the list ("Member agent votes" / "Member agent consents"). */
  label: string;
  busy: boolean;
  /** A human's tap for an agent that asked (POST member-agents/run with answers). */
  onAnswer: (member: string, choice: "yes" | "no") => void;
}

const BADGE_CLS: Record<AgentRow["state"], string> = {
  agent: "tk-chip-accent",
  human: "tk-chip-good",
  judge: "tk-chip-good",
  pending: "tk-chip-warn",
};

/**
 * Consent and vote rows from the member agents: "Tomas's agent · rule: accepts earlier turn", a source badge
 * (agent vs human) and, for an `ask`, the pending state with the human's Yes/No. Rendered only when a member
 * agent run has produced decisions for the open remedy set.
 */
export function AgentRows({ rows, label, busy, onAnswer }: AgentRowsProps) {
  if (rows.length === 0) return null;
  const asking = new Set(pendingAsks(rows).map((r) => r.key));
  return (
    <div className="tk-agent-rows">
      <ul className="tk-agent-list" aria-label={label}>
        {rows.map((r) => (
          <li key={r.key} className={`tk-agent-row tk-agent-${r.state}`} title={r.detail}>
            <span className="tk-agent-line">{r.line}</span>
            <span className="tk-agent-result">
              {r.state === "pending" ? (
                <span className="tk-small tk-agent-pending">
                  <IconClock /> {r.status}
                </span>
              ) : r.choice ? (
                <span className={`tk-agent-choice tk-agent-choice-${r.choice}`}>
                  {r.choice === "yes" ? <IconCheck /> : <IconX />} {r.choice}
                </span>
              ) : (
                <span className="tk-small tk-muted">{r.status}</span>
              )}
              <span className={`tk-chip ${BADGE_CLS[r.state]} tk-agent-badge`} title={badgeTip(r)}>
                {r.badge}
              </span>
            </span>
            {asking.has(r.key) && (
              <span className="tk-agent-ask">
                <span className="tk-small tk-muted">answer as {r.name}:</span>
                <button type="button" className="tk-btn tk-btn-sm" disabled={busy} onClick={() => onAnswer(r.member, "yes")}>
                  Yes
                </button>
                <button type="button" className="tk-btn tk-btn-sm" disabled={busy} onClick={() => onAnswer(r.member, "no")}>
                  No
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function badgeTip(r: AgentRow): string {
  switch (r.state) {
    case "agent":
      return "Recorded by the member's agent, inside the member's signed mandate (source: agent).";
    case "human":
      return "The agent asked; the member answered it themselves.";
    case "judge":
      return "The agent asked; the judge's click answered as the human (Judge Mode).";
    default:
      return "The agent's rule says ask: the question is in the member's inbox and waits for a human.";
  }
}
