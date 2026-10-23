/**
 * Pure helpers for the recovery path UI (docs/CONTRACTS.md "B6 recovery path") and the honest
 * run-mode labels. No React, no fetches: everything here is unit-tested.
 */

import type { BoardModel, InboxMessage, RecoveryInfo, RemedyInfo, RunMode, SessionInfo } from "./types";
import { fmtDate, fmtMoney } from "./format";

// ---------------------------------------------------------------------------------------------
// Run mode (header chip, trace chip, per-line badges)
// ---------------------------------------------------------------------------------------------

export interface ModeChip {
  cls: string;
  label: string;
  tip: string;
}

export const MODE_HELP =
  "live: the model is called right now and proposes every step. " +
  "replay: a recorded model run is replayed step by step (no key needed, nothing is invented). " +
  "rules: a deterministic script performs the same tool sequence with no model. " +
  "In every mode the policy engine decides and humans approve.";

const MODE_CHIPS: Record<RunMode, ModeChip> = {
  live: { cls: "tk-chip-accent", label: "live AI", tip: "The treasurer called the model for this run." },
  replay: {
    cls: "tk-chip-replay",
    label: "AI replay",
    tip: "A recorded model run was replayed turn by turn from the cache; no model was called.",
  },
  rules: {
    cls: "tk-chip-pending",
    label: "rules mode",
    tip: "No model was used: a deterministic rules script ran the same tool sequence.",
  },
  engine: {
    cls: "tk-chip-pending",
    label: "engine (no agent)",
    tip: "The agent package was unavailable; the round engine ran directly.",
  },
};

export function normalizeMode(mode: unknown): RunMode | null {
  return mode === "live" || mode === "replay" || mode === "rules" || mode === "engine" ? mode : null;
}

/** Chip for a run mode, or null when there has been no run yet (never claims "live" by default). */
export function modeChip(mode: unknown): ModeChip | null {
  const m = normalizeMode(mode);
  return m ? MODE_CHIPS[m] : null;
}

/** Short per-line badge text. */
export function modeBadge(mode: unknown): string | null {
  const m = normalizeMode(mode);
  if (!m) return null;
  return m === "engine" ? "engine" : m;
}

export interface PayPalChip {
  cls: string;
  label: string;
  tip: string;
}

export const PAYPAL_NOT_CONFIGURED = "PayPal sandbox credentials not configured";
export const SIMULATE_DECLINE_LABEL = "Simulate Round 3 decline";

export function paypalChip(session: Pick<SessionInfo, "paypal_live">): PayPalChip {
  return session.paypal_live
    ? { cls: "tk-chip-good", label: "PayPal sandbox", tip: "Charges and payouts go to the PayPal sandbox." }
    : {
        cls: "tk-chip-warn",
        label: "PayPal not configured",
        tip:
          `${PAYPAL_NOT_CONFIGURED}: rounds cannot be collected or paid. Replayed rounds 1-2 still show, and ` +
          `"${SIMULATE_DECLINE_LABEL}" in Judge mode stands in for Run Round 3 (labelled, no PayPal call), ` +
          "so the recovery path (simulated invoice) can be walked.",
      };
}

/**
 * Run Round 3 needs live sandbox charges: credentials AND seeded vault ids. Otherwise Judge mode offers
 * the simulated decline instead (REPLAY- captures, Ravi DECLINED with decline_source "simulated").
 */
export function canChargeLive(session: Pick<SessionInfo, "paypal_live" | "vault_seeded"> | null | undefined): boolean {
  return Boolean(session?.paypal_live) && session?.vault_seeded !== false;
}

// ---------------------------------------------------------------------------------------------
// Remedies
// ---------------------------------------------------------------------------------------------

const TYPE_WORDS: Record<string, string[]> = {
  buffer_advance: ["buffer_advance", "buffer advance", "open advance"],
  repay_by_invoice: ["repay_by_invoice", "invoice"],
  turn_swap: ["turn_swap", "swap", "turn ("],
  retry_on_date: ["retry_on_date", "retry", "retries", "decline "],
};

/** Problems from the set's `problems` list that concern this remedy (matched by its type's wording). */
export function remedyProblems(remedy: Pick<RemedyInfo, "type">, problems: string[] | null | undefined): string[] {
  const words = TYPE_WORDS[remedy.type] ?? [remedy.type];
  return (problems ?? []).filter((p) => {
    const lower = p.toLowerCase();
    return words.some((w) => lower.includes(w));
  });
}

/** Problems that match no remedy (set-level: frozen, empty set, wrong round, ...). */
export function setProblems(remedies: RemedyInfo[], problems: string[] | null | undefined): string[] {
  return (problems ?? []).filter((p) => !remedies.some((r) => remedyProblems(r, [p]).length > 0));
}

export const REMEDY_TITLE: Record<string, string> = {
  buffer_advance: "Buffer advance",
  repay_by_invoice: "Repay by invoice",
  turn_swap: "Turn swap",
  retry_on_date: "Retry on payday",
};

export function nameOf(board: BoardModel | null, handle: string | null | undefined): string {
  if (!handle) return "";
  if (handle === "priya") return "Priya";
  return board?.members.find((m) => m.handle === handle)?.name ?? handle;
}

/** One-line headline for a remedy card, e.g. "Buffer advance · $192.53 now". */
export function remedyHeadline(r: RemedyInfo, board: BoardModel | null, currency = "USD"): string {
  const title = REMEDY_TITLE[r.type] ?? r.type;
  const amount = r.params.amount ? fmtMoney(r.params.amount, currency) : null;
  switch (r.type) {
    case "buffer_advance":
      return `${title}${amount ? ` · ${amount} now` : ""}`;
    case "repay_by_invoice":
      return `${title}${amount ? ` · ${amount}` : ""}${r.params.due_date ? ` due ${fmtDate(r.params.due_date)}` : ""}`;
    case "turn_swap":
      return `${title} · ${nameOf(board, r.member)} ↔ ${nameOf(board, r.params.swap_with)}`;
    case "retry_on_date":
      return `${title}${r.params.due_date ? ` · ${fmtDate(r.params.due_date)}` : ""}`;
    default:
      return title;
  }
}

// ---------------------------------------------------------------------------------------------
// Votes, consents, approval
// ---------------------------------------------------------------------------------------------

export interface VoteBar {
  yes: number;
  no: number;
  needed: number;
  total: number;
  /** 0..100: yes votes as a share of the quorum. */
  pct: number;
  passed: boolean;
}

export function voteBar(rec: RecoveryInfo | null): VoteBar | null {
  const v = rec?.votes;
  if (!v) return null;
  const needed = Math.max(1, Number(v.needed) || 1);
  const yes = Math.max(0, Number(v.yes) || 0);
  return {
    yes,
    no: Math.max(0, Number(v.no) || 0),
    needed,
    total: Number(v.total) || needed,
    pct: Math.min(100, Math.round((yes / needed) * 100)),
    passed: Boolean(v.passed) || yes >= needed,
  };
}

export type ConsentState = "given" | "refused" | "pending";

export function consentRows(rec: RecoveryInfo | null): { member: string; state: ConsentState }[] {
  const c = rec?.consents;
  if (!c) return [];
  return c.needed.map((member) => ({
    member,
    state: c.given.includes(member) ? "given" : c.refused.includes(member) ? "refused" : "pending",
  }));
}

/** Statuses in which the set is still waiting on people. */
const OPEN = new Set(["proposed", "voting", "approved"]);
const DONE = new Set(["executed", "repaid"]);

export function hasActiveSet(rec: RecoveryInfo | null): boolean {
  return Boolean(rec && rec.set_id && rec.status !== "none");
}

/** First DECLINED contribution (what "Start recovery" targets), lowest round first. */
export function declinedContribution(board: BoardModel | null): { round_n: number; member: string } | null {
  const c = (board?.contributions ?? [])
    .filter((x) => x.status === "DECLINED")
    .sort((a, b) => a.round_n - b.round_n)[0];
  return c ? { round_n: c.round_n, member: c.member } : null;
}

export interface RecoveryButtons {
  start: string | null;
  consent: string | null;
  vote: string | null;
  approve: string | null;
  markPaid: string | null;
}

/**
 * Why each recovery judge button is disabled (null = enabled). The server re-checks everything;
 * this only keeps the demo from offering a step that would certainly answer 409.
 */
export function recoveryButtons(board: BoardModel | null, rec: RecoveryInfo | null): RecoveryButtons {
  const active = hasActiveSet(rec);
  const status = rec?.status ?? "none";
  const open = active && OPEN.has(status);
  // The server accepts consents and votes only while the set is "voting" (a "proposed" set answers 409).
  const voting = active && status === "voting";
  const notVoting = status === "proposed" ? "The vote is not open yet" : `Remedy set is ${status}`;
  const declined = declinedContribution(board);
  const consents = consentRows(rec);
  const bar = voteBar(rec);
  const invoice = rec?.invoice ?? null;
  return {
    // Re-planning while a set is open is allowed: the new proposal supersedes it.
    start: declined
      ? null
      : `No declined contribution to recover (run round 3 with 'Ravi declines' on, or "${SIMULATE_DECLINE_LABEL}")`,
    consent: !active
      ? "Start the recovery first"
      : !voting
        ? notVoting
        : consents.length === 0
          ? "No turn swap in this proposal, so no consent is needed"
          : consents.every((c) => c.state !== "pending")
            ? "Every affected member has answered"
            : null,
    vote: !active
      ? "Start the recovery first"
      : !voting
        ? notVoting
        : bar?.passed
          ? "Quorum already reached"
          : null,
    approve: !active
      ? "Start the recovery first"
      : DONE.has(status)
        ? "Already executed"
        : !open
          ? `Remedy set is ${status}`
          : null,
    markPaid: !invoice
      ? "No invoice yet (Priya's approval sends it)"
      : invoice.status === "PAID"
        ? "Invoice already paid"
        : invoice.status !== "SENT"
          ? `Invoice was not sent (${invoice.status ?? "unknown"})`
          : null,
  };
}

// ---------------------------------------------------------------------------------------------
// Inbox (English subtitles for the known Hindi texts)
// ---------------------------------------------------------------------------------------------

/** The demo reply and the outbound templates from app/engine/inbox.py, Hindi -> English. */
const KNOWN_TRANSLATIONS: { re: RegExp; en: (m: RegExpExecArray) => string }[] = [
  {
    re: /^शुक्रवार को सैलरी आएगी, तब भर दूंगा$/,
    en: () => "Salary comes on Friday, I will pay then.",
  },
  {
    re: /^नमस्ते (.+?), राउंड (\d+) का आपका (.+?) का भुगतान नहीं हो पाया। आप कब भर पाएंगे\?$/,
    en: (m) => `Hi ${m[1]}, your round ${m[2]} contribution of ${m[3]} did not go through. When can you pay?`,
  },
  {
    re: /^नमस्ते (.+?), सर्कल ने आपका हिस्सा भर दिया है। (.+?) का PayPal इनवॉइस, देय तिथि (.+?)।$/,
    en: (m) => `Hi ${m[1]}, the circle covered your share. Here is your PayPal invoice for ${m[2]}, due ${m[3]}.`,
  },
];

const DEVANAGARI = /[ऀ-ॿ]/;

export function isHindi(msg: Pick<InboxMessage, "lang" | "text">): boolean {
  return msg.lang === "hi" || DEVANAGARI.test(msg.text ?? "");
}

/**
 * English subtitle for a message: a translation the server returned for it, a known template,
 * or an honest "no translation" note. English messages get no subtitle.
 */
export function subtitleFor(msg: Pick<InboxMessage, "id" | "lang" | "text">, known: Record<number, string> = {}): string | null {
  if (!isHindi(msg)) return null;
  if (known[msg.id]) return known[msg.id];
  const text = (msg.text ?? "").trim();
  for (const t of KNOWN_TRANSLATIONS) {
    const m = t.re.exec(text);
    if (m) return t.en(m);
  }
  return "Hindi (no English translation on file)";
}

/** Messages oldest first; ids break timestamp ties. */
export function sortedInbox(messages: InboxMessage[] | null | undefined): InboxMessage[] {
  return [...(messages ?? [])].sort((a, b) => {
    const ta = a.ts ? Date.parse(a.ts) : 0;
    const tb = b.ts ? Date.parse(b.ts) : 0;
    return ta - tb || a.id - b.id;
  });
}

// ---------------------------------------------------------------------------------------------
// Schedule changes (board animation)
// ---------------------------------------------------------------------------------------------

/** Handles whose rotation position changed between two orders (both lists are full rotations). */
export function movedHandles(before: string[] | null | undefined, after: string[] | null | undefined): string[] {
  if (!before?.length || !after?.length) return [];
  return after.filter((h, i) => before.indexOf(h) !== i);
}
