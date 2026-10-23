/**
 * Pure mapping from the BoardModel to what the rotation board draws.
 *
 * Resources are members in rotation order; the time axis covers the rounds by due date; every
 * contribution becomes one event (coloured by status) and every round adds a "pot" milestone on
 * the recipient's row at the due date. Nothing here touches Bryntum, so it is unit-tested and the
 * CSS-grid fallback renders from the same data.
 */

import type {
  BoardModel,
  ContributionInfo,
  ContributionStatus,
  MemberInfo,
  RoundInfo,
  RoundStatus,
  Wallet,
} from "../types";
import { fmtDate, fmtDateTime, fmtMoney, parseCalendarDate, parseDateTime } from "../format";
import { emptyContribution } from "../state";

export const DAY_MS = 86_400_000;

/** Status colours (same hues in both themes; the label and icon always accompany the colour). */
export const STATUS_COLOR: Record<ContributionStatus, string> = {
  PENDING: "#898781",
  COMPLETED: "#0ca30c",
  DECLINED: "#d03b3b",
  COVERED: "#fab219",
};

export const ROUND_COLOR: Record<RoundStatus, string> = {
  PENDING: "#898781",
  COLLECTING: "#2a78d6",
  COLLECTED: "#fab219",
  PAID: "#0ca30c",
  VOID: "#d03b3b",
};

export const STATUS_LABEL: Record<ContributionStatus, string> = {
  PENDING: "Pending",
  COMPLETED: "Completed",
  DECLINED: "Declined",
  COVERED: "Covered",
};

/** FontAwesome (bundled with the scheduler package; `fa-*` names from the free solid set). */
export function iconCls(name: string): string {
  return `fa fa-${name}`;
}

const STATUS_ICON: Record<ContributionStatus, string> = {
  PENDING: "clock",
  COMPLETED: "check",
  DECLINED: "xmark",
  COVERED: "life-ring",
};

export interface BoardResource {
  id: string;
  name: string;
  handle: string;
  wallet: Wallet;
  lang: string;
  vaulted: boolean;
  rotationPos: number;
  /** Round in which this member receives the pot, when known. */
  recipientRound: number | null;
}

export interface ContributionMeta {
  kind: "contribution";
  roundN: number;
  member: string;
  memberName: string;
  status: ContributionStatus;
  orderId: string | null;
  captureId: string | null;
  gross: string | null;
  fee: string | null;
  net: string | null;
  declineCode: string | null;
  declineSource: "api" | "webhook" | null;
  attempt: number;
  webhookVerified: boolean;
  predicted: boolean;
  label: string | null;
  dueAt: string;
}

export interface PotMeta {
  kind: "pot";
  roundN: number;
  recipient: string;
  recipientName: string;
  status: RoundStatus;
  dueAt: string;
  paidAt: string | null;
  batchId: string | null;
  itemId: string | null;
  payoutStatus: string | null;
  amount: string | null;
}

export type EventMeta = ContributionMeta | PotMeta;

export interface BoardEvent {
  id: string;
  resourceId: string;
  name: string;
  startDate: Date;
  endDate: Date;
  cls: string;
  eventColor: string;
  iconCls: string;
  /** Turnkeeper data for tooltips and the detail strip (read with `record.get("tk")`). */
  tk: EventMeta;
}

export interface BoardTimeRange {
  id: string;
  name: string;
  startDate: Date;
  endDate?: Date;
  cls: string;
}

export interface BoardMapping {
  resources: BoardResource[];
  events: BoardEvent[];
  timeRanges: BoardTimeRange[];
  startDate: Date;
  endDate: Date;
  viewPreset: string;
  /** Pixels per time-axis tick (month for monthly circles, week for weekly ones). */
  tickSize: number;
  /** Days before the due date that a contribution bar starts. */
  windowDays: number;
  currency: string;
}

export function addDays(d: Date, days: number): Date {
  return new Date(d.getTime() + days * DAY_MS);
}

/** Members sorted by rotation position (ties and missing positions fall back to the charter order). */
export function orderedMembers(board: BoardModel): MemberInfo[] {
  const rotation = board.circle?.rotation ?? [];
  const pos = (m: MemberInfo) => {
    const idx = rotation.indexOf(m.handle);
    if (idx >= 0) return idx + 1;
    return Number.isFinite(m.rotation_pos) && m.rotation_pos > 0 ? m.rotation_pos : Number.MAX_SAFE_INTEGER;
  };
  return [...board.members].sort((a, b) => pos(a) - pos(b) || a.handle.localeCompare(b.handle));
}

export function sortedRounds(board: BoardModel): RoundInfo[] {
  return [...board.rounds].sort((a, b) => a.n - b.n);
}

/** The contribution record for (round, member), or a PENDING placeholder. */
export function contributionFor(board: BoardModel, roundN: number, member: string): ContributionInfo {
  const found = board.contributions.find((c) => c.round_n === roundN && c.member === member);
  return found ?? emptyContribution(roundN, member);
}

/** Smallest gap in days between consecutive due dates (30 monthly / 7 weekly when unknown). */
export function cadenceDays(board: BoardModel, dues: Date[]): number {
  let gap = Number.POSITIVE_INFINITY;
  for (let i = 1; i < dues.length; i += 1) {
    const d = (dues[i].getTime() - dues[i - 1].getTime()) / DAY_MS;
    if (d > 0 && d < gap) gap = d;
  }
  if (Number.isFinite(gap)) return Math.round(gap);
  return board.circle?.cadence === "weekly" ? 7 : 30;
}

/** Short chip text: "$200.00 ✓", "DECLINED: API decline: sandbox-injected", ... */
export function chipLabel(c: ContributionInfo, amount: string, currency: string): string {
  const money = fmtMoney(c.gross ?? amount, currency);
  switch (c.status) {
    case "COMPLETED":
      return `${money}${c.webhook_verified ? " verified" : " captured"}`;
    case "DECLINED":
      return `Declined${c.label ? `: ${c.label}` : c.decline_code ? `: ${c.decline_code}` : ""}`;
    case "COVERED":
      return `Covered${c.label ? `: ${c.label}` : " by buffer"}`;
    case "PENDING":
    default:
      return c.predicted ? `${money} at risk` : `${money} due`;
  }
}

export function potLabel(round: RoundInfo, currency: string): string {
  if (round.payout?.amount) return `Pot ${fmtMoney(round.payout.amount, currency)}`;
  return `Pot · round ${round.n}`;
}

export function contributionMeta(c: ContributionInfo, member: MemberInfo | undefined, round: RoundInfo): ContributionMeta {
  return {
    kind: "contribution",
    roundN: round.n,
    member: c.member,
    memberName: member?.name ?? c.member,
    status: c.status,
    orderId: c.order_id ?? null,
    captureId: c.capture_id ?? null,
    gross: c.gross ?? null,
    fee: c.fee ?? null,
    net: c.net ?? null,
    declineCode: c.decline_code ?? null,
    declineSource: c.decline_source ?? null,
    attempt: c.attempt ?? 0,
    webhookVerified: Boolean(c.webhook_verified),
    predicted: Boolean(c.predicted),
    label: c.label ?? null,
    dueAt: round.due_at,
  };
}

export function potMeta(round: RoundInfo, recipient: MemberInfo | undefined): PotMeta {
  return {
    kind: "pot",
    roundN: round.n,
    recipient: round.recipient,
    recipientName: recipient?.name ?? round.recipient,
    status: round.status,
    dueAt: round.due_at,
    paidAt: round.paid_at ?? null,
    batchId: round.payout?.batch_id ?? null,
    itemId: round.payout?.item_id ?? null,
    payoutStatus: round.payout?.status ?? null,
    amount: round.payout?.amount ?? null,
  };
}

export function contributionCls(meta: ContributionMeta): string {
  const parts = ["tk-contrib", `tk-st-${meta.status.toLowerCase()}`];
  if (meta.predicted) parts.push("tk-predicted");
  if (meta.webhookVerified) parts.push("tk-verified");
  return parts.join(" ");
}

export function potCls(meta: PotMeta): string {
  return `tk-pot tk-pot-${meta.status.toLowerCase()}`;
}

/** Build everything the board needs. Returns null when the board has no rounds yet. */
export function mapBoard(board: BoardModel | null): BoardMapping | null {
  if (!board || !board.rounds?.length) return null;
  const currency = board.circle?.currency ?? "USD";
  const amount = board.circle?.amount ?? "";
  const members = orderedMembers(board);
  const byHandle = new Map(members.map((m) => [m.handle, m]));
  const rounds = sortedRounds(board);

  const dues = rounds.map((r) => parseCalendarDate(r.due_at) ?? new Date(NaN));
  const valid = dues.filter((d) => !Number.isNaN(d.getTime()));
  if (!valid.length) return null;
  const gap = cadenceDays(board, valid);
  const weekly = gap <= 10;
  const windowDays = Math.max(2, Math.min(16, gap - 3));

  const resources: BoardResource[] = members.map((m) => ({
    id: m.handle,
    name: m.name,
    handle: m.handle,
    wallet: m.payout_wallet,
    lang: m.lang,
    vaulted: Boolean(m.vaulted),
    rotationPos: m.rotation_pos,
    recipientRound: rounds.find((r) => r.recipient === m.handle)?.n ?? null,
  }));

  const events: BoardEvent[] = [];
  const timeRanges: BoardTimeRange[] = [];

  rounds.forEach((round, i) => {
    const due = dues[i];
    if (Number.isNaN(due.getTime())) return;
    const barStart = addDays(due, -windowDays);
    const barEnd = addDays(due, -1);

    timeRanges.push({
      id: `round-${round.n}`,
      name: `Round ${round.n} · ${byHandle.get(round.recipient)?.name ?? round.recipient}`,
      startDate: barStart,
      endDate: addDays(due, 1),
      cls: `tk-round tk-round-${round.status.toLowerCase()}`,
    });

    for (const m of members) {
      const c = contributionFor(board, round.n, m.handle);
      const meta = contributionMeta(c, m, round);
      events.push({
        id: `c-${round.n}-${m.handle}`,
        resourceId: m.handle,
        name: chipLabel(c, amount, currency),
        startDate: barStart,
        endDate: barEnd,
        cls: contributionCls(meta),
        eventColor: STATUS_COLOR[c.status] ?? STATUS_COLOR.PENDING,
        iconCls: iconCls(STATUS_ICON[c.status] ?? "clock"),
        tk: meta,
      });
    }

    if (byHandle.has(round.recipient)) {
      const meta = potMeta(round, byHandle.get(round.recipient));
      events.push({
        id: `pot-${round.n}`,
        resourceId: round.recipient,
        name: potLabel(round, currency),
        startDate: due,
        endDate: due,
        cls: potCls(meta),
        eventColor: ROUND_COLOR[round.status] ?? ROUND_COLOR.PENDING,
        iconCls: iconCls("coins"),
        tk: meta,
      });
    }
  });

  const now = parseDateTime(board.circle?.virtual_now);
  if (now) {
    timeRanges.push({ id: "now", name: "now", startDate: now, cls: "tk-now" });
  }

  const first = valid[0];
  const last = valid[valid.length - 1];
  return {
    resources,
    events,
    timeRanges,
    startDate: addDays(first, -(windowDays + 4)),
    endDate: addDays(last, 20),
    viewPreset: weekly ? "weekAndMonth" : "monthAndYear",
    tickSize: 170,
    windowDays,
    currency,
  };
}

// ---------------------------------------------------------------------------------------------
// Tooltip / detail rows (shared by the Bryntum tooltip and the fallback grid)
// ---------------------------------------------------------------------------------------------

export interface DetailRow {
  k: string;
  v: string;
  mono?: boolean;
}

export function detailTitle(meta: EventMeta): string {
  if (meta.kind === "pot") return `Round ${meta.roundN} pot → ${meta.recipientName}`;
  return `Round ${meta.roundN} · ${meta.memberName} · ${STATUS_LABEL[meta.status] ?? meta.status}`;
}

export function detailRows(meta: EventMeta, currency = "USD"): DetailRow[] {
  if (meta.kind === "pot") {
    const rows: DetailRow[] = [
      { k: "Status", v: meta.status },
      { k: "Due", v: fmtDate(meta.dueAt) },
      { k: "Amount", v: meta.amount ? fmtMoney(meta.amount, currency) : "not paid yet" },
    ];
    if (meta.payoutStatus) rows.push({ k: "Payout", v: meta.payoutStatus });
    rows.push({ k: "Batch id", v: meta.batchId ?? "—", mono: true });
    rows.push({ k: "Item id", v: meta.itemId ?? "—", mono: true });
    if (meta.paidAt) rows.push({ k: "Paid at", v: fmtDateTime(meta.paidAt) });
    return rows;
  }
  const rows: DetailRow[] = [
    { k: "Status", v: STATUS_LABEL[meta.status] ?? meta.status },
    { k: "Due", v: fmtDate(meta.dueAt) },
  ];
  if (meta.label) rows.push({ k: "Note", v: meta.label });
  if (meta.declineCode) rows.push({ k: "Decline", v: `${meta.declineCode}${meta.declineSource ? ` (${meta.declineSource})` : ""}` });
  rows.push({ k: "Order id", v: meta.orderId ?? "—", mono: true });
  rows.push({ k: "Capture id", v: meta.captureId ?? "—", mono: true });
  if (meta.gross) rows.push({ k: "Gross", v: fmtMoney(meta.gross, currency) });
  if (meta.fee) rows.push({ k: "PayPal fee", v: fmtMoney(meta.fee, currency) });
  if (meta.net) rows.push({ k: "Net", v: fmtMoney(meta.net, currency) });
  if (meta.attempt > 1) rows.push({ k: "Attempt", v: String(meta.attempt) });
  rows.push({ k: "Webhook", v: meta.webhookVerified ? "capture verified" : "not verified yet" });
  if (meta.predicted) rows.push({ k: "Forecast", v: "flagged at risk" });
  return rows;
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** HTML for a Bryntum tooltip. Every value is escaped; Bryntum inserts the result via innerHTML. */
export function tooltipHtml(meta: EventMeta, currency = "USD"): string {
  const rows = detailRows(meta, currency)
    .map(
      (r) =>
        `<div class="tk-tip-row"><span class="tk-tip-k">${escapeHtml(r.k)}</span>` +
        `<span class="tk-tip-v${r.mono ? " tk-mono" : ""}">${escapeHtml(r.v)}</span></div>`,
    )
    .join("");
  return `<div class="tk-tip"><div class="tk-tip-title">${escapeHtml(detailTitle(meta))}</div>${rows}</div>`;
}
