/**
 * Member join page (docs/CONTRACTS.md "Builder B"): pure mappers for the `/join/{invite}` states.
 * The PayPal return lands on `/join/{invite}?status=vaulted|cancelled|action_required|declined|error|
 * expired|invalid`; without `status` the page shows the stored join status from `GET /api/join/{invite}`.
 */

import type { JoinCard } from "./types";

export type JoinTone = "good" | "warn" | "critical" | "info";

export interface JoinStatusView {
  status: string;
  tone: JoinTone;
  title: string;
  body: string;
  /** Show "Continue to PayPal". */
  canContinue: boolean;
  /** Show the payout-wallet form. */
  showWallet: boolean;
}

const TITLES: Record<string, { tone: JoinTone; title: string; canContinue: boolean }> = {
  invited: { tone: "info", title: "You're invited", canContinue: true },
  pending_approval: { tone: "info", title: "Waiting for PayPal", canContinue: true },
  vaulted: { tone: "good", title: "You're in", canContinue: false },
  cancelled: { tone: "warn", title: "Cancelled on PayPal", canContinue: true },
  action_required: { tone: "warn", title: "Almost there", canContinue: true },
  declined: { tone: "critical", title: "PayPal declined", canContinue: true },
  error: { tone: "critical", title: "Something went wrong", canContinue: true },
  expired: { tone: "critical", title: "Invite expired", canContinue: false },
  invalid: { tone: "critical", title: "Invite not valid", canContinue: false },
};

const FALLBACK_BODY: Record<string, string> = {
  invited: "Review the circle below, then continue to PayPal to approve the schedule once.",
  pending_approval: "Waiting for your approval on PayPal.",
  vaulted: "PayPal saved your approval; contributions are collected on each due date.",
  cancelled: "You cancelled on PayPal. Nothing was saved and nothing will be charged.",
  action_required: "PayPal still needs you to finish approving. Tap Continue to PayPal again.",
  declined: "PayPal declined this payment method. Pick another funding source in PayPal, or ask the organizer.",
  error: "Something went wrong with PayPal. Nothing was saved; please try again.",
  expired: "This invite link has expired. Ask the organizer for a new one.",
  invalid: "This invite link is not valid. Check the link or ask the organizer for a new one.",
};

/**
 * The status the page shows: a fresh `?status=` from the PayPal return wins over the stored one, except
 * that a vaulted member always reads vaulted (a stale `?status=cancelled` after a later success).
 */
export function joinStatusView(card: JoinCard | null, urlStatus: string | null): JoinStatusView {
  const stored = card?.join?.join_status ?? (card?.vaulted ? "vaulted" : "invited");
  let status = urlStatus && TITLES[urlStatus] ? urlStatus : stored;
  if (card?.vaulted) status = "vaulted";
  const meta = TITLES[status] ?? { tone: "info" as JoinTone, title: status, canContinue: true };
  // The server's message is specific (e.g. the decline code) when it matches the shown status.
  const serverMsg = card && card.join?.join_status === status ? card.join.join_message : "";
  const closed = card?.circle?.status && card.circle.status !== "active";
  return {
    status,
    tone: meta.tone,
    title: meta.title,
    body: serverMsg || FALLBACK_BODY[status] || "",
    canContinue: meta.canContinue && !card?.vaulted && !closed,
    showWallet: status === "vaulted" || Boolean(card?.vaulted),
  };
}

/** Status for an error loading the card (410 expired, 404 invalid). */
export function statusForLoadError(httpStatus: number): "expired" | "invalid" | "error" {
  if (httpStatus === 410) return "expired";
  if (httpStatus === 404) return "invalid";
  return "error";
}

const EMAIL = /^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$/;

/** Same rule as the server (routes/join.py); empty means "keep the PayPal account that approved". */
export function walletEmailProblem(value: string, hasDefault: boolean): string | null {
  const v = value.trim();
  if (!v) return hasDefault ? null : "Enter the PayPal email that should receive your pot.";
  return EMAIL.test(v) ? null : "That does not look like an email address.";
}

/** Approve URLs must be PayPal's (sandbox) pages; anything else is refused client-side. */
export function isPayPalApproveUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && /(^|\.)paypal\.com$/.test(u.hostname);
  } catch {
    return false;
  }
}
