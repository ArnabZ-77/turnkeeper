/**
 * D1 learnings in the UI (docs/CONTRACTS.md "Builder E"): the PayPal balance context chip and the
 * UNCLAIMED re-route states. Pure; unit-tested.
 */

import { fmtMoney, parseDateTime } from "./format";
import type { PayPalBalanceInfo, PayoutAttemptInfo, PayoutEvent, RoundInfo } from "./types";

export interface Chip {
  cls: string;
  label: string;
  tip: string;
}

/** 24 h "hh:mm" in the viewer's time zone ("" for a bad value). */
export function hhmm(value: string | null | undefined): string {
  const d = parseDateTime(value);
  if (!d) return "";
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
}

const BALANCE_TIP =
  "Context only. PayPal's Balances API is account-wide and lags in the sandbox (readings older than 15 min " +
  "count as stale). The circle's own ledger is what gates a payout.";

/** "PayPal balance $X (refreshed hh:mm)", or null before any balance check. */
export function balanceChip(pb: PayPalBalanceInfo | null | undefined, currency = "USD"): Chip | null {
  if (!pb || pb.value === null || pb.value === undefined || pb.value === "") return null;
  const at = hhmm(pb.last_refreshed);
  const when = at ? ` (refreshed ${at})` : " (refresh time unknown)";
  const stale = pb.stale === true;
  return {
    cls: stale ? "tk-chip-warn" : pb.stale === false ? "tk-chip-muted" : "tk-chip-pending",
    label: `PayPal balance ${fmtMoney(pb.value, currency)}${when}${stale ? " · stale" : ""}`,
    tip: `${BALANCE_TIP}${pb.checked_at ? `\nLast checked ${hhmm(pb.checked_at)}.` : ""}`,
  };
}

export const CANCEL_UNAVAILABLE = "Cancel not available in sandbox";
export const PENDING_RETURN = "pending return";

export interface AttemptRow {
  rev: number;
  status: string;
  amount: string | null;
  itemId: string | null;
  superseded: boolean;
  /** Short state words for chips: "Cancel not available in sandbox", "pending return", "returned", "re-routed". */
  notes: string[];
  label: string | null;
}

export interface UnclaimedView {
  /** The current item is UNCLAIMED: offer Re-route. */
  canReroute: boolean;
  rerouted: boolean;
  rev: number;
  chips: Chip[];
  attempts: AttemptRow[];
}

function attemptNotes(a: PayoutAttemptInfo, superseded: boolean): string[] {
  const out: string[] = [];
  if (a.cancel_status === "cancel_failed") out.push(CANCEL_UNAVAILABLE);
  else if (a.cancel_status === "canceled") out.push("cancelled");
  if (a.return_status === "pending_return") out.push(PENDING_RETURN);
  else if (a.return_status === "returned") out.push("returned");
  if (!superseded && a.rev > 1) out.push("re-routed");
  return out;
}

/** Everything the receipt shows about a round's payout batches. */
export function unclaimedView(round: RoundInfo | null | undefined, currency = "USD"): UnclaimedView {
  const attemptsRaw = [...(round?.payout_attempts ?? [])].sort((a, b) => a.rev - b.rev);
  const rev = Math.max(round?.payout_rev ?? 0, ...attemptsRaw.map((a) => a.rev), 0);
  const attempts: AttemptRow[] = attemptsRaw.map((a) => {
    const superseded = a.rev < rev;
    return {
      rev: a.rev,
      status: a.status ?? "—",
      amount: a.amount,
      itemId: a.item_id,
      superseded,
      notes: attemptNotes(a, superseded),
      label: a.label,
    };
  });
  const status = (round?.payout?.status ?? "").toUpperCase();
  const chips: Chip[] = [];
  const rerouted = rev > 1;
  if (status === "UNCLAIMED") {
    chips.push({
      cls: "tk-chip-warn",
      label: "UNCLAIMED",
      tip: "The receiver has no PayPal account yet. Re-route waits for the batch, tries one cancel, then pays the member's other receiver.",
    });
  }
  if (rerouted) {
    chips.push({ cls: "tk-chip-accent", label: `re-routed (r${rev})`, tip: "A new batch went to the member's other receiver; the circle ledger counts the pot once." });
  }
  if (attemptsRaw.some((a) => a.cancel_status === "cancel_failed")) {
    chips.push({
      cls: "tk-chip-pending",
      label: CANCEL_UNAVAILABLE,
      tip: "PayPal refused the cancel of the UNCLAIMED item in the sandbox. No ledger refund was written; the business account fronted the re-route.",
    });
  }
  const pr = round?.pending_return;
  if (pr) {
    chips.push({
      cls: "tk-chip-warn",
      label: `${PENDING_RETURN} ${fmtMoney(pr.amount, currency)}`,
      tip: "Funds of the superseded item come back to the business account when PayPal returns it (RETURNED / REFUNDED / CANCELED webhook).",
    });
  }
  return { canReroute: status === "UNCLAIMED", rerouted, rev, chips, attempts };
}

/** Fold a re-route `payout` event into its round (revision and pending return) ahead of the board refetch. */
export function applyRerouteToRound(round: RoundInfo, ev: PayoutEvent): RoundInfo {
  if (typeof ev.rev !== "number") return round;
  const pending = ev.pending_return
    ? {
        amount: ev.pending_return.amount,
        items: [{ rev: ev.rev - 1, item_id: ev.pending_return.item_id, amount: ev.pending_return.amount }],
        label: ev.pending_return.label || PENDING_RETURN,
      }
    : (round.pending_return ?? null);
  return { ...round, payout_rev: ev.rev, pending_return: pending };
}
