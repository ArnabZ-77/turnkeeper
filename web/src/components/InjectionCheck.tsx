import { useEffect, useState } from "react";
import { api } from "../api";
import { fmtMoney } from "../format";
import type { InjectionCheckView } from "../types";
import { IconCheck } from "./icons";

export interface InjectionCheckProps {
  circleId: string;
  /** Re-fetch when the proposal changes. */
  setId: string | null;
  currency: string;
}

/** True when the short member's reply carried amounts (eval s06): only then is the check shown. */
export function hasPlanted(view: InjectionCheckView | null): view is InjectionCheckView {
  return Boolean(view && view.planted && view.planted.length > 0);
}

/** One line per planted amount, computed by the server's validate_remedy_set (never written by hand). */
export function verdictText(p: InjectionCheckView["planted"][number], contribution: string, currency: string): string {
  if (p.used) return `${fmtMoney(p.amount, currency)} is used by the proposal`;
  if (p.policy_if_used === null) return "not used: no invoice in this proposal";
  if (p.policy_if_used.length === 0)
    return `not used: legal, but the charter's remedy asks for ${fmtMoney(contribution, currency)}`;
  return `not used; if it were, the policy says: ${p.policy_if_used.join("; ")}`;
}

/**
 * "Member text is data": the amounts a member typed, struck through against the proposal's amounts, with the
 * policy verdict the server computed for each one. Renders nothing for a reply without amounts.
 */
export function InjectionCheck({ circleId, setId, currency }: InjectionCheckProps) {
  const [view, setView] = useState<InjectionCheckView | null>(null);
  useEffect(() => {
    let live = true;
    api
      .injectionCheck(circleId)
      .then((v) => live && setView(v))
      .catch(() => live && setView(null));
    return () => {
      live = false;
    };
  }, [circleId, setId]);
  if (!hasPlanted(view)) return null;
  const sha = (view.charter_sha256 ?? "").slice(0, 4);
  return (
    <div className="tk-injection" data-testid="injection-check">
      <span className="tk-chip tk-chip-good" title="The reply reaches the model as a quoted data block; amounts come from the frozen charter">
        <IconCheck /> member text is data: amounts come from charter {sha}…
      </span>
      <ul className="tk-injection-list">
        {view.planted.map((p) => (
          <li key={`${p.text}-${p.amount}`}>
            <s className="tk-injection-planted">{p.text}</s>{" "}
            <span className="tk-muted tk-small">{verdictText(p, view.contribution ?? "", currency)}</span>
          </li>
        ))}
      </ul>
      {view.proposal?.invoice && (
        <p className="tk-small tk-injection-used">
          Proposal invoice: <strong>{fmtMoney(view.proposal.invoice, currency)}</strong> · from charter
        </p>
      )}
    </div>
  );
}
