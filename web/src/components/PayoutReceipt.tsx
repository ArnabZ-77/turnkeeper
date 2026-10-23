import { useState } from "react";
import type { MemberInfo, PayoutEvent, RoundInfo } from "../types";
import { copyText, fmtDateTime, fmtMoney } from "../format";
import { IconCheck, IconClock, IconCoins, IconCopy, IconX } from "./icons";

export interface PayoutReceiptProps {
  /** Latest `payout` SSE event, if any. */
  payout: PayoutEvent | null;
  rounds: RoundInfo[];
  members: MemberInfo[];
  currency?: string;
}

interface ReceiptView {
  roundN: number;
  status: string;
  amount: string;
  recipient: string;
  wallet: string;
  batchId: string | null;
  itemId: string | null;
  paidAt: string | null;
}

/** Prefer the live event; otherwise show the most recent round that has a payout on the board. */
export function receiptFrom(payout: PayoutEvent | null, rounds: RoundInfo[]): ReceiptView | null {
  if (payout) {
    const round = rounds.find((r) => r.n === payout.round_n);
    return {
      roundN: payout.round_n,
      status: payout.status,
      amount: payout.amount,
      recipient: payout.recipient,
      wallet: payout.wallet,
      batchId: payout.batch_id ?? round?.payout?.batch_id ?? null,
      itemId: payout.item_id ?? round?.payout?.item_id ?? null,
      paidAt: round?.paid_at ?? null,
    };
  }
  const withPayout = rounds.filter((r) => r.payout).sort((a, b) => b.n - a.n);
  const r = withPayout[0];
  if (!r || !r.payout) return null;
  return {
    roundN: r.n,
    status: r.payout.status,
    amount: r.payout.amount,
    recipient: r.recipient,
    wallet: "",
    batchId: r.payout.batch_id,
    itemId: r.payout.item_id,
    paidAt: r.paid_at,
  };
}

export function statusTone(status: string): "good" | "warn" | "critical" | "pending" {
  const s = status.toUpperCase();
  if (s === "SUCCESS" || s === "PAID") return "good";
  if (s === "UNCLAIMED" || s === "PENDING" | s === "ONHOLD" || s === "NEW" || s === "PROCESSING") return "warn";
  if (s === "FAILED" || s === "BLOCKED" || s === "RETURNED" || s === "CANCELED" || s === "DENIED" || s === "REFUNDED") {
    return "critical";
  }
  return "pending";
}

function CopyId({ value }: { value: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="tk-muted">—</span>;
  const onCopy = async () => {
    if (await copyText(value)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <>
      <code>{value}</code>{" "}
      <button type="button" className="tk-copy" onClick={onCopy} aria-label="Copy id">
        <IconCopy /> {copied ? "copied" : "copy"}
      </button>
    </>
  );
}

export function PayoutReceipt({ payout, rounds, members, currency = "USD" }: PayoutReceiptProps) {
  const view = receiptFrom(payout, rounds);
  const nextRecipient = rounds.filter((r) => r.status !== "PAID" && r.status !== "VOID").sort((a, b) => a.n - b.n)[0];
  const nameOf = (handle: string) => members.find((m) => m.handle === handle)?.name ?? handle;
  const walletOf = (handle: string) => members.find((m) => m.handle === handle)?.payout_wallet ?? "";

  if (!view) {
    return (
      <section className="tk-card" aria-label="Payout receipt">
        <h2 className="tk-card-title">Payout receipt</h2>
        <p className="tk-muted">
          No payout yet.
          {nextRecipient && (
            <>
              {" "}
              Round {nextRecipient.n} pays <strong>{nameOf(nextRecipient.recipient)}</strong> via{" "}
              {walletOf(nextRecipient.recipient) || "PayPal"} once every contribution is verified.
            </>
          )}
        </p>
      </section>
    );
  }

  const tone = statusTone(view.status);
  const Icon = tone === "good" ? IconCheck : tone === "critical" ? IconX : tone === "warn" ? IconClock : IconCoins;
  return (
    <section className="tk-card" aria-label="Payout receipt">
      <h2 className="tk-card-title">
        <span>Payout receipt · round {view.roundN}</span>
      </h2>
      <div className={`tk-receipt-status tk-chip tk-chip-${tone}`}>
        <Icon />
        {view.status}
      </div>
      <div className="tk-receipt-amount">{fmtMoney(view.amount, currency)}</div>
      <dl className="tk-kv">
        <dt>Recipient</dt>
        <dd>
          {nameOf(view.recipient)} <span className="tk-muted">via {view.wallet || walletOf(view.recipient) || "PayPal"}</span>
        </dd>
        <dt>Payout item</dt>
        <dd>
          <CopyId value={view.itemId} />
        </dd>
        <dt>Batch</dt>
        <dd>
          <CopyId value={view.batchId} />
        </dd>
        <dt>Paid at</dt>
        <dd>{view.paidAt ? fmtDateTime(view.paidAt) : <span className="tk-muted">pending</span>}</dd>
      </dl>
      <p className="tk-small tk-muted" style={{ margin: "8px 0 0" }}>
        Amount is the net pot: contributions minus PayPal fees, plus any approved buffer advance.
      </p>
    </section>
  );
}
