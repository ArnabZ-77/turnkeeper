/**
 * One-sentence summaries of the treasurer's tool results, so the trace reads as a story rather than
 * raw JSON (the JSON stays one click away). Pure and unit-tested.
 */

import { fmtMoney } from "./format";

type Json = Record<string, unknown>;

function isRecord(v: unknown): v is Json {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string | null {
  if (typeof v === "string" && v.trim()) return v.trim();
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return null;
}

function money(v: unknown): string | null {
  const s = str(v);
  return s && Number.isFinite(Number(s)) ? fmtMoney(s) : null;
}

/** Parse a tool result's text as a JSON object, or null when it is not one. */
export function parseResult(text: string): Json | null {
  const t = (text ?? "").trim();
  if (!t.startsWith("{")) return null;
  try {
    const v = JSON.parse(t);
    return isRecord(v) ? v : null;
  } catch {
    return null;
  }
}

/**
 * "Dev $200.00 COMPLETED, fee $7.47, net $192.53" for a charge, "ravi DECLINED (INSTRUMENT_DECLINED)"
 * for a decline, and a short key: value list for anything else. Null when the text is not JSON.
 */
export function resultSummary(text: string, tool?: string | null): string | null {
  const r = parseResult(text);
  if (!r) return null;
  const parts: string[] = [];
  const who = str(r.member) ?? str(r.recipient) ?? str(r.handle);
  const status = str(r.status) ?? str(r.order_status) ?? str(r.capture_status) ?? str(r.payout_status);
  const gross = money(r.gross) ?? money(r.amount);
  const head = [who, gross, status].filter(Boolean).join(" ");
  if (head) parts.push(head);
  const decline = str(r.decline_code);
  if (decline) parts.push(`decline ${decline}`);
  const fee = money(r.fee);
  if (fee) parts.push(`fee ${fee}`);
  const net = money(r.net);
  if (net) parts.push(`net ${net}`);
  if (typeof r.round_n === "number") parts.push(`round ${r.round_n}`);
  const error = str(r.error) ?? str(r.denied);
  if (error) parts.push(`error: ${error}`);
  if (!parts.length) {
    // Generic: the first few scalar fields.
    for (const [k, v] of Object.entries(r)) {
      const s = typeof v === "boolean" ? String(v) : str(v);
      if (s !== null) parts.push(`${k} ${s.length > 24 ? `${s.slice(0, 24)}…` : s}`);
      if (parts.length >= 4) break;
    }
  }
  if (!parts.length) return null;
  const sentence = parts.join(", ");
  return tool ? `${tool} → ${sentence}` : sentence;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11-03" -> "3 Nov" (no time zone involved), or null. */
export function recordedDay(iso: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!m) return null;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${Number(m[3])} ${month}` : null;
}

/**
 * The row's mode label: "AI replay · gemini-3.5-flash-lite · recorded 3 Nov", "live AI · gemini-...", or just
 * the base label (rules mode has no model). The base label comes from the mode chip.
 */
export function modeLabel(base: string, mode: string | undefined, model?: string, recorded?: string): string {
  const parts = [base];
  if (model && (mode === "live" || mode === "replay")) parts.push(model);
  const day = mode === "replay" ? recordedDay(recorded) : null;
  if (day) parts.push(`recorded ${day}`);
  return parts.join(" · ");
}

/** Why the treasurer calls a tool, when the model gave no words of its own (rules mode, or a silent turn). */
export const TOOL_WHY: Record<string, string> = {
  get_circle: "reads the frozen charter and members",
  get_round_matrix: "checks who has paid this round",
  get_member_history: "reads this member's record before proposing anything",
  collect_round: "charges each vaulted member for the round",
  charge_vaulted_member: "retries one member's charge",
  get_order_details: "confirms the capture on PayPal",
  get_available_balance: "checks the pot can cover the payout",
  create_pot_payout: "pays the pot to this round's recipient",
  get_payout_status: "checks the payout batch",
  record_note: "leaves a note for the organizer",
  list_inbox_replies: "reads the member's reply (as data, never as instructions)",
  record_commitment: "records the date the member promised",
  propose_remedies: "proposes remedies from the charter's menu",
  open_vote: "opens the vote",
  get_vote_status: "checks the vote",
  request_organizer_approval: "asks the organizer to approve",
  execute_remedies: "carries out the approved remedies",
  reschedule_turns: "moves turns after an agreed swap",
};

const OBJECT_KEYS: [string, string][] = [
  ["order_id", "order"],
  ["capture_id", "capture"],
  ["invoice_id", "invoice"],
  ["payout_batch_id", "payout batch"],
  ["batch_id", "payout batch"],
];

function findIds(v: unknown, out: Map<string, string>, depth: number): boolean {
  if (depth > 3) return false;
  let qr = false;
  if (Array.isArray(v)) {
    for (const x of v) qr = findIds(x, out, depth + 1) || qr;
    return qr;
  }
  if (!isRecord(v)) return false;
  for (const [key, label] of OBJECT_KEYS) {
    const id = str(v[key]);
    if (id && !out.has(label)) out.set(label, id.length > 32 ? `${id.slice(0, 32)}…` : id);
  }
  if (v.qr === true || str(v.qr) || str(v.qr_url)) qr = true;
  for (const x of Object.values(v)) if (typeof x === "object" && x !== null) qr = findIds(x, out, depth + 1) || qr;
  return qr;
}

/**
 * The PayPal objects a tool result names: "invoice INV2-AB12 + QR", "order 5O19 · capture 3C67", "payout batch
 * 8XQ...". Null when the result names none. Plain text; the trace renders it as text.
 */
export function paypalObjects(text: string): string | null {
  const r = parseResult(text);
  if (!r) return null;
  const ids = new Map<string, string>();
  const qr = findIds(r, ids, 0);
  if (!ids.size) return null;
  const parts = [...ids].map(([label, id]) => `${label} ${id}${label === "invoice" && qr ? " + QR" : ""}`);
  return parts.join(" · ");
}

/** PayPal's debug id, shown only on a decline row (it is what PayPal support asks for). */
export function declineDebugId(text: string): string | null {
  const r = parseResult(text);
  if (!r || !str(r.decline_code)) return null;
  return str(r.debug_id) ?? str(r.paypal_debug_id);
}
