import { useState } from "react";
import type { CircleInfo, MemberInfo, RoundInfo } from "../types";
import { copyText, fmtDateTime, fmtMoney } from "../format";
import { balanceChip } from "../payouts";
import { IconCopy, IconShield } from "./icons";

export interface CharterCardProps {
  circle: CircleInfo | null;
  members: MemberInfo[];
  rounds: RoundInfo[];
}

const FEE_POLICY_LABEL: Record<string, string> = {
  net_pot: "net pot (fees come out of the pot)",
  gross_up: "gross up (members cover fees)",
  organizer_absorbs: "organizer absorbs fees",
};

export function CharterCard({ circle, members, rounds }: CharterCardProps) {
  const [copied, setCopied] = useState(false);

  if (!circle) {
    return (
      <section className="tk-card" aria-label="Charter">
        <h2 className="tk-card-title">Charter</h2>
        <p className="tk-muted">Loading the circle…</p>
      </section>
    );
  }

  const hash = circle.charter_sha256 ?? "";
  const balance = balanceChip(circle.paypal_balance, circle.currency);
  const paid = rounds.filter((r) => r.status === "PAID").length;
  const onCopy = async () => {
    if (await copyText(hash)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <section className="tk-card tk-charter" aria-label="Charter">
      <h2 className="tk-card-title">
        <span>Charter · {circle.name}</span>
        <span className={`tk-chip ${circle.mandate_present ? "tk-chip-good" : "tk-chip-pending"}`}>
          <IconShield />
          {circle.mandate_present ? "payout mandate approved" : "no payout mandate"}
        </span>
      </h2>
      <dl className="tk-kv">
        <dt>Contribution</dt>
        <dd>
          {fmtMoney(circle.amount, circle.currency)} {circle.cadence}, {members.length} members, {rounds.length} rounds
          {rounds.length ? ` (${paid} paid)` : ""}
        </dd>
        <dt>Fees</dt>
        <dd>{FEE_POLICY_LABEL[circle.fee_policy] ?? circle.fee_policy}</dd>
        <dt>Buffer</dt>
        <dd>
          {fmtMoney(circle.buffer_balance, circle.currency)} <span className="tk-muted">organizer-seeded, disclosed</span>
        </dd>
        <dt>Circle available</dt>
        <dd>
          {fmtMoney(circle.circle_available, circle.currency)} <span className="tk-muted">net ledger, the payout gate</span>
        </dd>
        <dt>PayPal balance</dt>
        <dd>
          {balance ? (
            <span className={`tk-chip ${balance.cls}`} title={balance.tip}>
              <span className="tk-dot" />
              {balance.label}
            </span>
          ) : (
            <span className="tk-muted" title="Read from PayPal's Balances API when a payout is checked; context only, never the gate.">
              not checked yet (context only)
            </span>
          )}
        </dd>
        <dt>Charter hash</dt>
        <dd>
          <span className="tk-hash" title={hash}>
            {hash ? `${hash.slice(0, 12)}…` : "—"}
          </span>{" "}
          {hash && (
            <button type="button" className="tk-copy" onClick={onCopy} aria-label="Copy charter hash">
              <IconCopy /> {copied ? "copied" : "copy"}
            </button>
          )}
        </dd>
        <dt>Status</dt>
        <dd>
          {circle.status} · virtual clock {fmtDateTime(circle.virtual_now)}
        </dd>
        <dt>Judge flags</dt>
        <dd>
          <span className={circle.flags?.ravi_declines ? "tk-flag-on" : "tk-muted"}>
            Ravi declines {circle.flags?.ravi_declines ? "on" : "off"}
          </span>
          {" · "}
          <span className={circle.flags?.next_payout_unclaimed ? "tk-flag-on" : "tk-muted"}>
            next payout unclaimed {circle.flags?.next_payout_unclaimed ? "on" : "off"}
          </span>
        </dd>
      </dl>
    </section>
  );
}
