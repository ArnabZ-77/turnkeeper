import type { BoardModel, InvoiceInfo, RecoveryInfo, RemedyInfo } from "../types";
import { fmtDate, fmtDateTime, fmtMoney, shortId } from "../format";
import { consentRows, nameOf, remedyHeadline, remedyProblems, setProblems, voteBar } from "../recovery";
import { IconCheck, IconClock, IconX } from "./icons";
import type { AgentRow } from "../mandates";
import { AgentRows } from "./AgentRows";

const STATUS_TEXT: Record<string, string> = {
  invalid: "proposal rejected by the charter",
  proposed: "proposed",
  voting: "vote open",
  approved: "approved",
  executed: "executed",
  repaid: "repaid",
  failed: "failed",
  superseded: "superseded",
};

export interface RemedyCardProps {
  remedy: RemedyInfo;
  problems: string[];
  board: BoardModel | null;
  currency: string;
}

/** One remedy: what it does, why, and whether the charter allows it. */
export function RemedyCard({ remedy, problems, board, currency }: RemedyCardProps) {
  const own = remedyProblems(remedy, problems);
  const ok = own.length === 0;
  const executed = remedy.status === "executed" || remedy.status === "scheduled";
  return (
    <li className={`tk-remedy tk-remedy-${remedy.type}${executed ? " tk-remedy-done" : ""}`}>
      <div className="tk-remedy-head">
        <strong>{remedyHeadline(remedy, board, currency)}</strong>
        <span className={`tk-chip ${ok ? "tk-chip-good" : "tk-chip-critical"}`} title={ok ? "validate_remedy_set found no problem" : own.join("\n")}>
          {ok ? <IconCheck /> : <IconX />}
          {ok ? "charter OK" : `${own.length} problem${own.length > 1 ? "s" : ""}`}
        </span>
      </div>
      <p className="tk-remedy-reason" title={remedy.reason}>
        {remedy.reason}
      </p>
      {!ok && (
        <ul className="tk-remedy-problems">
          {own.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <span className="tk-muted tk-small">
        {remedy.status}
        {remedy.label ? ` · ${remedy.label}` : ""}
        {remedy.params.swap_with && board ? ` · partner ${nameOf(board, remedy.params.swap_with)}` : ""}
      </span>
    </li>
  );
}

export interface VotePanelProps {
  recovery: RecoveryInfo;
  board: BoardModel | null;
  busy: boolean;
  /** Why approval is not possible yet (null = Priya can approve). */
  approveBlocked: string | null;
  onApprove: () => void;
  /** Member agents (flag member_mandates): rows for the agents' answers; absent or empty renders nothing extra. */
  agentRows?: AgentRow[];
  onAnswerAsk?: (member: string, choice: "yes" | "no") => void;
}

/** Quorum bar, consents of the affected members and the organizer's one tap. */
export function VotePanel({ recovery, board, busy, approveBlocked, onApprove, agentRows, onAnswerAsk }: VotePanelProps) {
  const bar = voteBar(recovery);
  const consents = consentRows(recovery);
  // Member agents (flag member_mandates): their rows sit under the vote bar and in the consent column.
  const consentAgents = (agentRows ?? []).filter((r) => r.kind === "consent");
  const voteAgents = (agentRows ?? []).filter((r) => r.kind === "vote");
  const answer = (m: string, c: "yes" | "no") => onAnswerAsk?.(m, c);
  // A member whose agent already has a row is shown by that row, not twice.
  const consentChips = consentAgents.length ? consents.filter((c) => !consentAgents.some((a) => a.member === c.member)) : consents;
  const approval = recovery.approval;
  const approved = approval?.status === "approved";
  return (
    <div className="tk-vote">
      {bar && (
        <div className="tk-vote-row">
          <div className="tk-vote-label">
            <span>
              Members' vote: <strong>{bar.yes}</strong> yes / {bar.needed} needed
              {bar.no ? ` · ${bar.no} no` : ""}
            </span>
            <span className="tk-muted tk-small">
              {bar.passed ? "quorum reached" : recovery.votes?.closes_at ? `closes ${fmtDateTime(recovery.votes.closes_at)}` : ""}
            </span>
          </div>
          <div
            className={`tk-meter${bar.passed ? " tk-meter-done" : ""}`}
            role="meter"
            aria-valuemin={0}
            aria-valuemax={bar.needed}
            aria-valuenow={bar.yes}
            aria-label="Yes votes toward quorum"
          >
            <span style={{ width: `${bar.pct}%` }} />
          </div>
          {voteAgents.length > 0 && <AgentRows rows={voteAgents} label="Member agent votes" busy={busy} onAnswer={answer} />}
        </div>
      )}
      <div className="tk-vote-row">
        <span className="tk-vote-label">Consent of the affected members</span>
        {consents.length === 0 ? (
          <span className="tk-muted tk-small">No turn swap proposed, so no consent is needed.</span>
        ) : (
          <>
            {consentAgents.length > 0 && <AgentRows rows={consentAgents} label="Member agent consents" busy={busy} onAnswer={answer} />}
            {consentChips.length > 0 && (
          <span className="tk-chips">
            {consentChips.map((c) => (
              <span
                key={c.member}
                className={`tk-chip ${c.state === "given" ? "tk-chip-good" : c.state === "refused" ? "tk-chip-critical" : "tk-chip-pending"}`}
              >
                {c.state === "given" ? <IconCheck /> : c.state === "refused" ? <IconX /> : <span className="tk-dot" />}
                {nameOf(board, c.member)}: {c.state === "given" ? "consents" : c.state === "refused" ? "refused" : "waiting"}
              </span>
            ))}
          </span>
            )}
          </>
        )}
      </div>
      <div className="tk-vote-row tk-vote-approve">
        <span className="tk-vote-label">
          Organizer: Priya{" "}
          <span className="tk-muted tk-small">
            {approved ? `approved ${fmtDateTime(approval?.at)}` : approval?.status === "requested" ? "approval requested" : "not asked yet"}
          </span>
        </span>
        {approved ? (
          <span className="tk-chip tk-chip-good tk-approved-chip">
            <IconCheck /> Approved by Priya
          </span>
        ) : approveBlocked !== null ? (
          // Not a live-looking primary before quorum: the button says what it waits for.
          <button type="button" className="tk-btn tk-btn-wait" disabled title={approveBlocked}>
            <IconClock /> {approveBlocked}
          </button>
        ) : (
          <button
            type="button"
            className="tk-btn tk-btn-primary"
            disabled={busy}
            title="Priya's one tap: the policy engine re-checks every remedy, then executes"
            onClick={onApprove}
          >
            <IconCheck /> Approve as Priya
          </button>
        )}
      </div>
      {recovery.blockers && recovery.blockers.length > 0 && !approved && (
        <p className="tk-muted tk-small tk-vote-blockers">Waiting on: {recovery.blockers.join(" · ")}</p>
      )}
    </div>
  );
}

/** Invoice status chip with the QR thumbnail when the server has one. */
export function InvoiceChip({ invoice, currency }: { invoice: InvoiceInfo; currency: string }) {
  const paid = invoice.status === "PAID";
  const src = invoice.qr_data && invoice.qr_data.startsWith("data:image/") ? invoice.qr_data : invoice.qr;
  return (
    <div className={`tk-invoice${paid ? " tk-invoice-paid" : ""}`}>
      {src && <img className="tk-invoice-qr" src={src} alt="Invoice QR code" width={72} height={72} />}
      <div className="tk-invoice-body">
        <span className="tk-chips">
          <span className={`tk-chip ${paid ? "tk-chip-good" : "tk-chip-warn"}`}>
            <span className="tk-dot" />
            Invoice {invoice.status}
          </span>
          {invoice.simulated && (
            <span className="tk-chip tk-chip-pending" title="No PayPal credentials: the invoice was simulated, not sent through PayPal">
              simulated
            </span>
          )}
        </span>
        <span>
          {fmtMoney(invoice.amount, currency)} from {invoice.member}
          {invoice.due_date ? `, due ${fmtDate(invoice.due_date)}` : ""}
        </span>
        <code className="tk-muted" title={invoice.id}>
          {shortId(invoice.id, 18)}
        </code>
      </div>
    </div>
  );
}

export interface RecoveryPanelProps {
  recovery: RecoveryInfo;
  board: BoardModel | null;
  busy: boolean;
  approveBlocked: string | null;
  onApprove: () => void;
  agentRows?: AgentRow[];
  onAnswerAsk?: (member: string, choice: "yes" | "no") => void;
}

/** The whole recovery story for one shortfall: commitment, remedies, vote, invoice. */
export function RecoveryPanel({ recovery, board, busy, approveBlocked, onApprove, agentRows, onAnswerAsk }: RecoveryPanelProps) {
  const currency = board?.circle.currency ?? "USD";
  const general = setProblems(recovery.remedies, recovery.problems);
  const member = nameOf(board, recovery.member);
  return (
    <section className={`tk-card tk-recovery${agentRows && agentRows.length > 0 ? " tk-recovery-agents" : ""}`} aria-label="Recovery">
      <h2 className="tk-card-title">
        <span>
          Recovery · {member} · round {recovery.round_n}
        </span>
        <span className="tk-chips">
          <span className={`tk-chip ${recovery.status === "invalid" || recovery.status === "failed" ? "tk-chip-critical" : recovery.status === "repaid" || recovery.status === "executed" ? "tk-chip-good" : "tk-chip-accent"}`}>
            <span className="tk-dot" />
            {STATUS_TEXT[recovery.status] ?? recovery.status}
          </span>
          {recovery.source && (
            <span className="tk-chip" title="Who proposed this remedy set">
              by {recovery.source === "agent" ? "the AI treasurer" : "the rules"}
            </span>
          )}
        </span>
      </h2>
      {recovery.commitment && (
        <p className="tk-recovery-commit">
          {member} committed to pay {fmtMoney(recovery.commitment.amount, currency)} on{" "}
          <strong>{fmtDate(recovery.commitment.date)}</strong>
          <span className="tk-muted"> (from {recovery.commitment.source === "rules" ? "the reply, parsed by rules" : recovery.commitment.source})</span>
        </p>
      )}
      <div className="tk-recovery-grid">
        <ol className="tk-remedies">
          {recovery.remedies.map((r) => (
            <RemedyCard key={r.id} remedy={r} problems={recovery.problems} board={board} currency={currency} />
          ))}
          {general.map((p) => (
            <li key={p} className="tk-remedy tk-remedy-problem">
              <span className="tk-chip tk-chip-critical">
                <IconX /> charter problem
              </span>{" "}
              {p}
            </li>
          ))}
        </ol>
        <div className="tk-recovery-side">
          <VotePanel
            recovery={recovery}
            board={board}
            busy={busy}
            approveBlocked={approveBlocked}
            onApprove={onApprove}
            agentRows={agentRows}
            onAnswerAsk={onAnswerAsk}
          />
          {recovery.invoice && <InvoiceChip invoice={recovery.invoice} currency={currency} />}
        </div>
      </div>
    </section>
  );
}
