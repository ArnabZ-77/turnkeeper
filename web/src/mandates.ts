/**
 * Pure helpers for member mandates and member agents (docs/CONTRACTS.md "Member mandates").
 *
 * The feature is OFF by default. Nothing here runs a request or renders on its own: the App only
 * fetches `GET /mandates` when the board says the flag is on, and every component built on these
 * helpers is hidden otherwise, so the existing UI is untouched. No React, no fetches: unit-tested.
 */

import type {
  AgentChoice,
  AgentDecision,
  AgentKind,
  AnswerSource,
  BoardModel,
  MandatesView,
  MemberMandateInfo,
  OverchargeResult,
  RecoveryInfo,
} from "./types";
import { fmtDate, fmtMoney } from "./format";

/** The two honest labels, shown wherever a mandate is shown (the server sends the same text; these are the fallback). */
export const MANDATE_LABEL =
  "AP2-style Intent Mandate, our own implementation; PayPal AP2 APIs are not public, we do not claim AP2 support";
export const SIGNATURE_LABEL = "server-attested HMAC signature, not a key held by the member";

/** Story variant name the server accepts with the reset step. */
export const STORY_VARIANT = "member_agents";

// ---------------------------------------------------------------------------------------------
// Is it on?
// ---------------------------------------------------------------------------------------------

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** The board's flags carry `member_mandates: true` only while the feature is on for the circle. */
export function mandatesOn(board: Pick<BoardModel, "circle"> | null | undefined): boolean {
  return board?.circle?.flags?.member_mandates === true;
}

/** `?member_agents=1` in the page URL opts in to Story B on the next restart (the default story is untouched). */
export function memberAgentsRequested(search: string | null | undefined): boolean {
  if (!search) return false;
  const v = new URLSearchParams(search).get("member_agents");
  return v === "1" || v === "true";
}

/** The story variant a restart should ask for: the circle already runs Story B, or the URL opted in. */
export function storyVariant(
  board: Pick<BoardModel, "circle"> | null | undefined,
  storyVariantNow: string | undefined,
  search: string | null | undefined,
): string | undefined {
  if (storyVariantNow === STORY_VARIANT) return STORY_VARIANT;
  if (board?.circle?.flags?.member_agents_story === true) return STORY_VARIANT;
  return memberAgentsRequested(search) ? STORY_VARIANT : undefined;
}

// ---------------------------------------------------------------------------------------------
// Mandates card
// ---------------------------------------------------------------------------------------------

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

/** Validate the `GET /mandates` body; null for anything that is not a usable, enabled view. */
export function parseMandates(raw: unknown): MandatesView | null {
  if (!isRecord(raw) || raw.enabled !== true || !Array.isArray(raw.members)) return null;
  const members: MemberMandateInfo[] = [];
  for (const m of raw.members) {
    if (!isRecord(m) || typeof m.member !== "string") continue;
    const rules = isRecord(m.rules)
      ? {
          accept_earlier_swap: m.rules.accept_earlier_swap === true,
          max_advance: str(m.rules.max_advance) ?? "",
          require_ask_for_later_swap: m.rules.require_ask_for_later_swap === true,
          summary: str(m.rules.summary) ?? "",
        }
      : null;
    members.push({
      member: m.member,
      name: str(m.name) ?? m.member,
      present: m.present === true,
      signature_verified: m.signature_verified === true,
      status: str(m.status) ?? "",
      max_charge_per_round: str(m.max_charge_per_round),
      max_advance: str(m.max_advance),
      expires: str(m.expires),
      approved_at: str(m.approved_at),
      charter_sha256: str(m.charter_sha256),
      rules,
      signature: str(m.signature),
    });
  }
  return {
    circle_id: str(raw.circle_id) ?? "",
    enabled: true,
    charter_sha256: str(raw.charter_sha256) ?? "",
    label: str(raw.label) || MANDATE_LABEL,
    signature_label: str(raw.signature_label) || SIGNATURE_LABEL,
    members,
  };
}

export interface SignatureChip {
  cls: string;
  label: string;
  tip: string;
}

/** The "signature verified" chip, or an honest failure that names why (expired, charter changed, ...). */
export function signatureChip(m: Pick<MemberMandateInfo, "present" | "signature_verified" | "status">): SignatureChip {
  if (!m.present) {
    return { cls: "tk-chip-pending", label: "no mandate", tip: "This member has no signed mandate: their agent always asks them." };
  }
  if (m.signature_verified) {
    return {
      cls: "tk-chip-good",
      label: "signature verified",
      tip: `The server re-checked the HMAC, the circle, the member, the charter hash and the expiry. ${SIGNATURE_LABEL}.`,
    };
  }
  const why = m.status && m.status !== "ok" ? m.status : "does not verify";
  return { cls: "tk-chip-critical", label: `not valid: ${why}`, tip: `The mandate is not accepted: ${why}. Charges and agent answers stay blocked.` };
}

/** Plain-language swap rules for one mandate (the limit, advance cap and expiry are shown separately). */
export function ruleLines(m: Pick<MemberMandateInfo, "rules">): string[] {
  const r = m.rules;
  if (!r) return [];
  const lines = [
    r.accept_earlier_swap ? "accepts earlier turn" : "asks before earlier turn",
    r.require_ask_for_later_swap ? "asks before later turn" : "accepts later turn",
  ];
  return lines;
}

export interface MandateRow {
  member: string;
  name: string;
  limit: string;
  expires: string;
  rules: string;
  /** "advance up to $200.00 · expires May 1, 2027" */
  meta: string;
  signature: string | null;
  chip: SignatureChip;
}

export function mandateRows(view: MandatesView | null, currency = "USD"): MandateRow[] {
  if (!view) return [];
  return view.members.map((m) => ({
    member: m.member,
    name: m.name,
    limit: m.max_charge_per_round ? `${fmtMoney(m.max_charge_per_round, currency)} / round` : "no limit on file",
    expires: m.expires ? `expires ${fmtDate(m.expires)}` : "no expiry on file",
    rules: ruleLines(m).join(" · "),
    meta: [m.max_advance ? `advance up to ${fmtMoney(m.max_advance, currency)}` : null, m.expires ? `expires ${fmtDate(m.expires)}` : "no expiry on file"]
      .filter(Boolean)
      .join(" · "),
    signature: m.signature,
    chip: signatureChip(m),
  }));
}

/** `{verified} of {total} signatures verified`, for the card header. */
export function verifiedSummary(view: MandatesView | null): string {
  if (!view || view.members.length === 0) return "";
  const ok = view.members.filter((m) => m.signature_verified).length;
  return `${ok} of ${view.members.length} verified`;
}

// ---------------------------------------------------------------------------------------------
// Agent decisions (vote and consent rows)
// ---------------------------------------------------------------------------------------------

const KINDS: AgentKind[] = ["consent", "vote"];
const CHOICES: AgentChoice[] = ["yes", "no", "ask"];
const SOURCES: AnswerSource[] = ["agent", "member", "judge"];

/** Decisions out of any response that carries `decisions[]`; anything malformed is dropped, never thrown on. */
export function parseDecisions(raw: unknown): AgentDecision[] {
  const list = isRecord(raw) ? raw.decisions : raw;
  if (!Array.isArray(list)) return [];
  const out: AgentDecision[] = [];
  for (const d of list) {
    if (!isRecord(d) || typeof d.member !== "string") continue;
    const kind = KINDS.find((k) => k === d.kind);
    const choice = CHOICES.find((c) => c === d.choice);
    if (!kind || !choice) continue;
    const source = SOURCES.find((s) => s === d.source) ?? null;
    const recorded = d.recorded === true;
    out.push({
      member: d.member,
      kind,
      choice,
      reason: str(d.reason) ?? "",
      rule: str(d.rule) ?? "",
      explanation: str(d.explanation),
      explanation_mode: str(d.explanation_mode),
      trace: Array.isArray(d.trace) ? d.trace.filter((t): t is string => typeof t === "string") : undefined,
      recorded,
      source: recorded ? source : null,
      recorded_choice: d.recorded_choice === "yes" || d.recorded_choice === "no" ? d.recorded_choice : undefined,
      asked: d.asked === true,
    });
  }
  return out;
}

export interface AgentDecisions {
  setId: string | null;
  items: AgentDecision[];
}

export const NO_DECISIONS: AgentDecisions = { setId: null, items: [] };

/** Fold a run's decisions into the kept ones: newest per kind+member wins; a different set starts clean. */
export function mergeDecisions(prev: AgentDecisions, setId: string | null, incoming: AgentDecision[]): AgentDecisions {
  const keep = prev.setId === setId ? prev.items : [];
  const key = (d: AgentDecision) => `${d.kind}:${d.member}`;
  const map = new Map(keep.map((d) => [key(d), d] as const));
  for (const d of incoming) map.set(key(d), d);
  return { setId, items: [...map.values()] };
}

/** Decisions only make sense next to the remedy set they answered. */
export function decisionsFor(state: AgentDecisions, rec: Pick<RecoveryInfo, "set_id"> | null | undefined): AgentDecision[] {
  if (!rec?.set_id || state.setId !== rec.set_id) return [];
  return state.items;
}

/** The decision-table rows (`rule`) in the words a member would use. */
const RULE_WORDS: Record<string, string> = {
  swap_earlier: "accepts earlier turn",
  swap_earlier_ask: "asks before any swap",
  swap_later_ask: "asks before a later turn",
  swap_allowed: "accepts a later turn",
  auto_legal_covered: "charter-legal, shortfall covered",
  advance_over_cap: "advance is over the cap",
  not_legal: "not charter-legal",
  not_covered: "shortfall not covered",
  no_mandate: "no valid mandate",
};

export function ruleLabel(rule: string): string {
  return RULE_WORDS[rule] ?? rule.replace(/_/g, " ");
}

export type AgentRowState = "agent" | "human" | "judge" | "pending";

export interface AgentRow {
  key: string;
  member: string;
  name: string;
  kind: AgentKind;
  /** "Tomas's agent · rule: accepts earlier turn" */
  line: string;
  choice: "yes" | "no" | null;
  state: AgentRowState;
  /** Badge text: "agent", "human", "human (judge click)" or "pending". */
  badge: string;
  /** Short status for the row: "yes", "no", or "pending - agent asked the human". */
  status: string;
  /** The agent's own reason (the sentence the member would read). */
  detail: string;
}

const KIND_WORD: Record<AgentKind, string> = { consent: "consent", vote: "vote" };

function firstName(board: Pick<BoardModel, "members"> | null | undefined, handle: string): string {
  const full = board?.members.find((m) => m.handle === handle)?.name ?? handle;
  return full.split(/\s+/)[0] || handle;
}

/**
 * Rows for the vote panel. A decision that was recorded shows who recorded it (agent vs human);
 * an `ask` nobody answered is "pending - agent asked the human". A pending consent that the recovery
 * block now lists as given or refused was answered meanwhile (a Judge button). A pending vote stays pending
 * even after quorum (the member can still answer), and says so.
 */
export function agentRows(
  decisions: AgentDecision[],
  rec: RecoveryInfo | null | undefined,
  board: Pick<BoardModel, "members"> | null | undefined,
): AgentRow[] {
  const rows: AgentRow[] = [];
  for (const d of decisions) {
    const name = firstName(board, d.member);
    const rule = d.rule ? ` · rule: ${ruleLabel(d.rule)}` : "";
    const line = `${name}'s agent${rule}`;
    const base = { key: `${d.kind}:${d.member}`, member: d.member, name, kind: d.kind, line, detail: d.explanation || d.reason };
    if (d.recorded && d.source) {
      const choice = d.recorded_choice ?? (d.choice === "ask" ? null : d.choice);
      if (d.source === "agent") {
        rows.push({ ...base, choice, state: "agent", badge: "agent", status: choice ?? "" });
      } else if (d.source === "judge") {
        rows.push({ ...base, choice, state: "judge", badge: "human (judge click)", status: choice ?? "" });
      } else {
        rows.push({ ...base, choice, state: "human", badge: "human", status: choice ?? "" });
      }
      continue;
    }
    // Not recorded: only an ask can be pending.
    const given = d.kind === "consent" && rec?.consents?.given.includes(d.member);
    const refused = d.kind === "consent" && rec?.consents?.refused.includes(d.member);
    if (given || refused) {
      rows.push({
        ...base,
        choice: given ? "yes" : "no",
        state: "judge",
        badge: "human (judge click)",
        status: given ? "yes" : "no",
      });
      continue;
    }
    const late = d.kind === "vote" && rec?.votes?.passed ? "; quorum already reached" : "";
    rows.push({
      ...base,
      choice: null,
      state: "pending",
      badge: "pending",
      status: "pending - agent asked the human",
      detail: `${base.detail} (${KIND_WORD[d.kind]}${late || ""}: waiting for ${name})`,
    });
  }
  return rows.concat(rowsFromRecovery(rows, rec, board));
}

/**
 * Answers the server remembers (recovery view: votes.voters[].source, consents.sources) that this browser has no
 * decision for, e.g. after a page reload: the badge survives, the agent's rule wording does not.
 */
function rowsFromRecovery(
  have: AgentRow[],
  rec: RecoveryInfo | null | undefined,
  board: Pick<BoardModel, "members"> | null | undefined,
): AgentRow[] {
  const known = new Set(have.map((r) => r.key));
  const out: AgentRow[] = [];
  const add = (kind: AgentKind, member: string, choice: string, source: string | undefined) => {
    if (!source || (choice !== "yes" && choice !== "no") || known.has(`${kind}:${member}`)) return;
    const name = firstName(board, member);
    const state: AgentRowState = source === "agent" ? "agent" : source === "judge" ? "judge" : "human";
    const badge = state === "agent" ? "agent" : state === "judge" ? "human (judge click)" : "human";
    const line = state === "agent" ? `${name}'s agent` : name;
    out.push({
      key: `${kind}:${member}`,
      member,
      name,
      kind,
      line,
      choice,
      state,
      badge,
      status: choice,
      detail: `${KIND_WORD[kind]} recorded by ${state === "agent" ? "the member's agent" : "the member"}`,
    });
  };
  for (const v of rec?.votes?.voters ?? []) add("vote", v.member, v.choice, v.source);
  const c = rec?.consents;
  for (const m of c?.given ?? []) add("consent", m, "yes", c?.sources?.[m]);
  for (const m of c?.refused ?? []) add("consent", m, "no", c?.sources?.[m]);
  return out;
}

/** Members whose agent asked and who have not answered: the rows that get Yes/No taps. */
export function pendingAsks(rows: AgentRow[]): AgentRow[] {
  return rows.filter((r) => r.state === "pending");
}

/** Body for `POST .../member-agents/run` that answers one member's ask (the human's tap). */
export function answerBody(setId: string | null, member: string, choice: "yes" | "no"): { set_id?: string; answers: Record<string, string> } {
  return { ...(setId ? { set_id: setId } : {}), answers: { [member]: choice } };
}

// ---------------------------------------------------------------------------------------------
// Try a $230 charge
// ---------------------------------------------------------------------------------------------

export const OVERCHARGE_AMOUNT = "230.00";

export function parseOvercharge(raw: unknown): OverchargeResult | null {
  if (!isRecord(raw) || typeof raw.denied !== "boolean") return null;
  return {
    ok: raw.ok === true,
    denied: raw.denied,
    reason: str(raw.reason) ?? undefined,
    banner: str(raw.banner) ?? undefined,
    member: str(raw.member) ?? undefined,
    amount: str(raw.amount) ?? undefined,
    round_n: typeof raw.round_n === "number" ? raw.round_n : undefined,
    paypal_called: raw.paypal_called === true,
  };
}

export interface OverchargeView {
  tone: "critical" | "good";
  headline: string;
  reason: string;
  paypal: string;
}

/** What the inline result shows: the denial reason as the server wrote it, and that PayPal was not called. */
export function overchargeView(r: OverchargeResult | null, currency = "USD"): OverchargeView | null {
  if (!r) return null;
  const who = r.member ? r.member[0].toUpperCase() + r.member.slice(1) : "member";
  const amt = r.amount ? fmtMoney(r.amount, currency) : "the charge";
  const paypal = r.paypal_called ? "PayPal was called" : "PayPal was not called";
  if (r.denied) {
    return {
      tone: "critical",
      headline: `Denied: ${amt} on ${who}'s mandate`,
      reason: r.reason ?? r.banner ?? "denied by the policy engine",
      paypal,
    };
  }
  return {
    tone: "good",
    headline: `Allowed by policy: ${amt} on ${who}'s mandate`,
    reason: r.reason ?? "within the member's mandate; nothing was sent",
    paypal,
  };
}
