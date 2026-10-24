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
