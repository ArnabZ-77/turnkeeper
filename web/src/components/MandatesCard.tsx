import type { MandatesView } from "../types";
import { mandateRows, verifiedSummary } from "../mandates";
import { IconCheck, IconX } from "./icons";

export interface MandatesCardProps {
  view: MandatesView;
  currency: string;
}

/**
 * One signed member mandate per row: the charge limit, the expiry, the rules the member's agent follows
 * and the signature check. Rendered only while the member_mandates flag is on (the App decides), and the
 * two honest labels always sit under the rows.
 */
export function MandatesCard({ view, currency }: MandatesCardProps) {
  const rows = mandateRows(view, currency);
  const allOk = rows.length > 0 && rows.every((r) => r.chip.cls === "tk-chip-good");
  return (
    <section className="tk-card tk-mandates" aria-label="Member mandates">
      <h2 className="tk-card-title">
        <span>Member mandates</span>
        <span className={`tk-chip ${allOk ? "tk-chip-good" : "tk-chip-warn"}`} title="Signatures re-checked by the server against the current charter hash">
          <span className="tk-dot" />
          {verifiedSummary(view)}
        </span>
      </h2>
      <ul className="tk-mandate-list">
        {rows.map((r) => (
          <li key={r.member} className="tk-mandate">
            <div className="tk-mandate-top">
              <strong>{r.name}</strong>
              <span className="tk-mandate-limit">{r.limit}</span>
              <span className={`tk-chip ${r.chip.cls} tk-mandate-chip`} title={r.signature ? `${r.chip.tip}
sig ${r.signature}` : r.chip.tip}>
                {r.chip.cls === "tk-chip-good" ? <IconCheck /> : r.chip.cls === "tk-chip-critical" ? <IconX /> : <span className="tk-dot" />}
                {r.chip.label}
              </span>
            </div>
            <div className="tk-mandate-rules tk-small">
              {r.rules ? r.rules : <span className="tk-muted">no rules on file</span>}
            </div>
            <div className="tk-mandate-expiry tk-small tk-muted">{r.meta}</div>
          </li>
        ))}
      </ul>
      <div className="tk-mandate-labels tk-small">
        <p>
          <strong>Mandate:</strong> {view.label}
        </p>
        <p>
          <strong>Signature:</strong> {view.signature_label}
        </p>
      </div>
    </section>
  );
}
