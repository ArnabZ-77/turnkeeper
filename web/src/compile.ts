/**
 * Charter compiler screen (docs/CONTRACTS.md "Builder B"): pure mappers from `POST /api/circles/compile`
 * and `/charter/freeze` to what the card shows. Every row carries the compiler's own explanation and the
 * problems that name its field (problems are "field: ..." strings).
 */

import { fmtDate, fmtMoney } from "./format";
import type { BillingPlan, CharterJson, CompileResult, FeesInfo } from "./types";

export const FIELD_ORDER = [
  "name",
  "amount",
  "cadence",
  "start_date",
  "members",
  "rotation",
  "fee_policy",
  "buffer",
  "grace_days",
  "quorum",
  "remedy_menu",
  "languages",
] as const;

export const FIELD_LABEL: Record<string, string> = {
  name: "Name",
  amount: "Contribution",
  cadence: "Cadence",
  start_date: "Starts",
  members: "Members",
  rotation: "Pot order",
  fee_policy: "Fees",
  buffer: "Buffer",
  grace_days: "Grace period",
  quorum: "Quorum",
  remedy_menu: "If someone is short",
  languages: "Languages",
};

const FEE_POLICY: Record<string, string> = {
  net_pot: "come out of the pot",
  gross_up: "members cover them (grossed up)",
  organizer_absorbs: "the organizer pays them",
};

const REMEDY: Record<string, string> = {
  retry_on_date: "retry on payday",
  turn_swap: "swap turns",
  buffer_advance: "buffer advance",
  repay_by_invoice: "repay by PayPal invoice",
};

const LANG: Record<string, string> = { en: "English", hi: "Hindi" };

function firstName(c: CharterJson, handle: string): string {
  const m = c.members.find((x) => x.handle === handle);
  return m ? m.name.split(/\s+/)[0] : handle;
}

/** Human value of one charter field. */
export function fieldValue(c: CharterJson, field: string): string {
  switch (field) {
    case "name":
      return c.name;
    case "amount":
      return `${fmtMoney(c.amount, c.currency)} per member`;
    case "cadence":
      return c.cadence === "weekly" ? "weekly" : "monthly";
    case "start_date":
      return fmtDate(c.start_date);
    case "members":
      return c.members
        .map((m) => `${m.name.split(/\s+/)[0]}${m.track_record_rounds > 0 ? ` (${m.track_record_rounds} rounds before)` : " (new)"}`)
        .join(", ");
    case "rotation":
      return c.rotation.map((h) => firstName(c, h)).join(" → ");
    case "fee_policy":
      return `PayPal fees ${FEE_POLICY[c.fee_policy] ?? c.fee_policy}`;
    case "buffer":
      return `${fmtMoney(c.buffer?.seed, c.currency)} organizer-seeded, disclosed`;
    case "grace_days":
      return `${c.grace_days} day${c.grace_days === 1 ? "" : "s"} after the due date`;
    case "quorum": {
      const [k, n] = c.quorum.split("/");
      return `${k} of ${n} votes for big decisions`;
    }
    case "remedy_menu":
      return c.remedy_menu.map((r) => REMEDY[r] ?? r).join(", then ");
    case "languages":
      return c.languages.map((l) => LANG[l] ?? l).join(", ");
    default:
      return "";
  }
}

export interface CharterRow {
  field: string;
  label: string;
  value: string;
  explanation: string | null;
  /** The explanation before de-duplication (for the hover title). */
  fullExplanation?: string | null;
  problems: string[];
}

/** Problems that name `field` ("rotation: ..."), with the prefix removed. */
export function fieldProblems(field: string, problems: string[] | null | undefined): string[] {
  const prefix = `${field}:`;
  return (problems ?? []).filter((p) => p.startsWith(prefix)).map((p) => p.slice(prefix.length).trim());
}

/** Problems that name no known field (shown above the card). */
export function generalProblems(problems: string[] | null | undefined): string[] {
  return (problems ?? []).filter((p) => !FIELD_ORDER.some((f) => p.startsWith(`${f}:`)));
}

export function charterRows(result: Pick<CompileResult, "charter" | "explanations" | "problems">): CharterRow[] {
  const c = result.charter;
  if (!c) return [];
  let previous: string | null = null;
  return FIELD_ORDER.map((field) => {
    const raw = result.explanations?.[field] ?? null;
    // The same source quote under consecutive rows says nothing new: show it once.
    const explanation = raw && raw === previous ? SAME_AS_ABOVE : raw;
    if (raw) previous = raw;
    return {
      field,
      label: FIELD_LABEL[field],
      value: fieldValue(c, field),
      explanation,
      fullExplanation: raw,
      problems: fieldProblems(field, result.problems),
    };
  });
}

export const SAME_AS_ABOVE = "same source line as above";

/** "Fees ≈ $7.47 per contribution; pot paid net" (wording follows the fee policy). */
export function feeLine(fees: Pick<FeesInfo, "fee_policy" | "fee_estimate"> | null | undefined, currency = "USD"): string {
  if (!fees) return "";
  const each = `Fees ≈ ${fmtMoney(fees.fee_estimate, currency)} per contribution`;
  switch (fees.fee_policy) {
    case "net_pot":
      return `${each}; pot paid net`;
    case "gross_up":
      return `${each}; members cover them, pot paid in full`;
    case "organizer_absorbs":
      return `${each}; organizer pays them, pot paid in full`;
    default:
      return each;
  }
}

const UNIT: Record<string, [string, string]> = {
  DAY: ["day", "days"],
  WEEK: ["week", "weeks"],
  MONTH: ["month", "months"],
  YEAR: ["year", "years"],
};

/** One line per billing cycle, e.g. "6 × $200.00 every month from Nov 1, 2026 · variable amount". */
export function billingPlanLines(plan: BillingPlan | null | undefined): string[] {
  return (plan?.billing_cycles ?? []).map((bc) => {
    const price = bc.pricing_scheme?.price;
    const money = price ? fmtMoney(price.value, price.currency_code) : "—";
    const unit = UNIT[bc.frequency?.interval_unit ?? ""] ?? [String(bc.frequency?.interval_unit ?? "").toLowerCase(), "cycles"];
    const count = bc.frequency?.interval_count ?? 1;
    const every = count === 1 ? `every ${unit[0]}` : `every ${count} ${unit[1]}`;
    const from = bc.start_date ? ` from ${fmtDate(bc.start_date)}` : "";
    const model = bc.pricing_scheme?.pricing_model === "VARIABLE" ? " · variable amount" : "";
    return `${bc.total_cycles} × ${money} ${every}${from}${model}`;
  });
}

export interface CompileChip {
  cls: string;
  label: string;
  tip: string;
}

/** Honest chip for who produced the draft; the server's `mode_label` is the text. */
export function compileModeChip(r: Pick<CompileResult, "mode" | "source" | "mode_label">): CompileChip {
  const label = r.mode_label || (r.mode === "rules" ? `rules mode: ${r.source}` : r.mode);
  if (r.mode === "live") return { cls: "tk-chip-accent", label, tip: "The model read the rules just now; the same deterministic checks then built the charter." };
  if (r.mode === "replay") return { cls: "tk-chip-replay", label, tip: "A recorded model answer for this exact text was replayed (no model call)." };
  if (r.source === "template") {
    return { cls: "tk-chip-warn", label, tip: "Neither a model nor the rules parser could read enough (two members and an amount); the demo template was used." };
  }
  return { cls: "tk-chip-pending", label, tip: "No model was used: a deterministic rules parser read the text." };
}

/** First 12 hex characters of a charter hash, for the chip. */
export function shortHash(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "—";
}
