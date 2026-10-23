import { describe, expect, it } from "vitest";
import { demoBoard } from "./fixtures";
import {
  addDays,
  cadenceDays,
  chipLabel,
  contributionFor,
  detailRows,
  mapBoard,
  orderedMembers,
  tooltipHtml,
  type ContributionMeta,
  type PotMeta,
} from "../board/mapping";

describe("mapBoard", () => {
  it("orders resources by rotation and gives every resource and event an id", () => {
    const m = mapBoard(demoBoard());
    expect(m).not.toBeNull();
    expect(m!.resources.map((r) => r.id)).toEqual(["meena", "asha", "ravi"]);
    expect(m!.resources[0].recipientRound).toBe(1);
    const ids = m!.events.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every((id) => id.length > 0)).toBe(true);
    expect(m!.timeRanges.every((t) => t.id.length > 0)).toBe(true);
  });

  it("creates one contribution event per member per round plus one pot milestone per round", () => {
    const m = mapBoard(demoBoard())!;
    const contributions = m.events.filter((e) => e.tk.kind === "contribution");
    const pots = m.events.filter((e) => e.tk.kind === "pot");
    expect(contributions).toHaveLength(9);
    expect(pots).toHaveLength(3);
    const pot1 = pots.find((p) => p.tk.roundN === 1)!;
    expect(pot1.resourceId).toBe("meena");
    expect(pot1.startDate.getTime()).toBe(pot1.endDate.getTime());
    expect(pot1.name).toContain("577.59");
    expect(pot1.cls).toContain("tk-pot-paid");
  });

  it("colours and labels contributions by status", () => {
    const m = mapBoard(demoBoard())!;
    const byId = (id: string) => m.events.find((e) => e.id === id)!;
    expect(byId("c-1-meena").eventColor).toBe("#0ca30c");
    expect(byId("c-1-meena").name).toContain("verified");
    expect(byId("c-1-asha").name).toContain("captured");
    const declined = byId("c-2-ravi");
    expect(declined.eventColor).toBe("#d03b3b");
    expect(declined.name).toContain("API decline: sandbox-injected");
    expect(declined.cls).toContain("tk-st-declined");
    const pending = byId("c-3-asha");
    expect(pending.eventColor).toBe("#898781");
    expect(pending.name).toContain("$200.00");
  });

  it("builds a monthly axis around the due dates with the bars ending before the pot", () => {
    const m = mapBoard(demoBoard())!;
    expect(m.viewPreset).toBe("monthAndYear");
    const first = new Date(2026, 10, 1);
    const last = new Date(2027, 0, 1);
    expect(m.startDate.getTime()).toBeLessThan(addDays(first, -9).getTime());
    expect(m.endDate.getTime()).toBe(addDays(last, 20).getTime());
    const c = m.events.find((e) => e.id === "c-1-meena")!;
    const pot = m.events.find((e) => e.id === "pot-1")!;
    expect(c.endDate.getTime()).toBeLessThanOrEqual(pot.startDate.getTime());
    expect(m.timeRanges.find((t) => t.id === "now")).toBeTruthy();
    expect(m.timeRanges.find((t) => t.id === "round-2")?.cls).toContain("tk-round-collecting");
  });

  it("switches to a weekly preset for weekly cadences", () => {
    const board = demoBoard();
    board.circle.cadence = "weekly";
    board.rounds = board.rounds.map((r, i) => ({ ...r, due_at: `2026-11-${String(1 + 7 * i).padStart(2, "0")}` }));
    const m = mapBoard(board)!;
    expect(cadenceDays(board, [new Date(2026, 10, 1), new Date(2026, 10, 8)])).toBe(7);
    expect(m.viewPreset).toBe("weekAndMonth");
    expect(m.windowDays).toBeLessThan(7);
  });

  it("returns null without a board or rounds", () => {
    expect(mapBoard(null)).toBeNull();
    expect(mapBoard(demoBoard({ rounds: [] }))).toBeNull();
  });
});

describe("helpers", () => {
  it("orderedMembers falls back to rotation_pos when the handle is not in the rotation", () => {
    const board = demoBoard();
    board.circle.rotation = [];
    expect(orderedMembers(board).map((m) => m.handle)).toEqual(["meena", "asha", "ravi"]);
  });

  it("contributionFor returns a PENDING placeholder", () => {
    const c = contributionFor(demoBoard(), 3, "meena");
    expect(c.status).toBe("PENDING");
    expect(chipLabel(c, "200.00", "USD")).toBe("$200.00 due");
  });

  it("tooltipHtml escapes values and lists order/capture ids", () => {
    const meta: ContributionMeta = {
      kind: "contribution",
      roundN: 2,
      member: "ravi",
      memberName: "<Ravi>",
      status: "DECLINED",
      orderId: "ORDER&1",
      captureId: null,
      gross: null,
      fee: null,
      net: null,
      declineCode: "INSTRUMENT_DECLINED",
      declineSource: "api",
      attempt: 2,
      webhookVerified: false,
      predicted: true,
      label: "API decline",
      dueAt: "2026-12-01",
    };
    const html = tooltipHtml(meta);
    expect(html).toContain("&lt;Ravi&gt;");
    expect(html).toContain("ORDER&amp;1");
    expect(html).not.toContain("<Ravi>");
    const rows = detailRows(meta);
    expect(rows.find((r) => r.k === "Order id")?.v).toBe("ORDER&1");
    expect(rows.find((r) => r.k === "Capture id")?.v).toBe("—");
    expect(rows.find((r) => r.k === "Attempt")?.v).toBe("2");
  });

  it("detailRows for a pot lists the payout ids", () => {
    const meta: PotMeta = {
      kind: "pot",
      roundN: 1,
      recipient: "meena",
      recipientName: "Meena",
      status: "PAID",
      dueAt: "2026-11-01",
      paidAt: "2026-11-01T10:00:05Z",
      batchId: "B1",
      itemId: "I1",
      payoutStatus: "SUCCESS",
      amount: "577.59",
    };
    const rows = detailRows(meta);
    expect(rows.find((r) => r.k === "Batch id")?.v).toBe("B1");
    expect(rows.find((r) => r.k === "Item id")?.v).toBe("I1");
    expect(rows.find((r) => r.k === "Amount")?.v).toBe("$577.59");
  });
});
