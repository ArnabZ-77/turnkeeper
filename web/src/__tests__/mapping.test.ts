import { describe, expect, it } from "vitest";
import { demoBoard } from "./fixtures";
import {
  addDays,
  cadenceDays,
  chipWindowDays,
  DAY_MS,
  focusHandle,
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
    expect(pot1.name).toBe("Pot $578"); // whole dollars on the board; the cents stay in the tooltip
    expect(tooltipHtml(pot1.tk)).toContain("$577.59");
    expect(pot1.cls).toContain("tk-pot-paid");
  });

  it("colours and labels contributions by status", () => {
    const m = mapBoard(demoBoard())!;
    const byId = (id: string) => m.events.find((e) => e.id === id)!;
    expect(byId("c-1-meena").eventColor).toBe("#0a7f0a");
    // Fixture rows (REPLAY- ids) get the hatched replay style and are never called captured/verified.
    expect(byId("c-1-meena").name).toBe("$200");
    expect(byId("c-1-meena").cls).toContain("tk-replay");
    expect(byId("c-1-meena").cls).not.toContain("tk-verified");
    expect(tooltipHtml(byId("c-1-meena").tk)).toContain("seeded history");
    expect(tooltipHtml(byId("c-1-meena").tk)).not.toContain("capture verified");
    const live = demoBoard();
    live.contributions = live.contributions.map((c) =>
      c.order_id ? { ...c, order_id: c.order_id.replace("REPLAY-", "SBX-") } : c,
    );
    const lm = mapBoard(live)!;
    expect(lm.events.find((e) => e.id === "c-1-meena")!.cls).toContain("tk-verified");
    expect(lm.events.find((e) => e.id === "c-1-meena")!.cls).not.toContain("tk-replay");
    expect(tooltipHtml(lm.events.find((e) => e.id === "c-1-meena")!.tk)).toContain("capture verified");
    const declined = byId("c-2-ravi");
    expect(declined.eventColor).toBe("#d03b3b");
    expect(declined.name).toBe("Declined");
    expect(tooltipHtml(declined.tk)).toContain("API decline: sandbox-injected");
    expect(declined.cls).toContain("tk-st-declined");
    const pending = byId("c-3-asha");
    expect(pending.eventColor).toBe("#6f6d68");
    expect(pending.name).toBe("$200");
  });

  it("builds a monthly axis around the due dates with the bars ending before the pot", () => {
    const m = mapBoard(demoBoard())!;
    expect(m.viewPreset.base).toBe("monthAndYear");
    expect(m.viewPreset.headers.map((h) => h.unit)).toEqual(["month", "month"]);
    expect(m.viewPreset.headers[1].renderer?.()).toBe("");
    const first = new Date(2026, 10, 1);
    const last = new Date(2027, 0, 1);
    expect(m.startDate.getTime()).toBeLessThan(addDays(first, -9).getTime());
    expect(m.endDate.getTime()).toBe(addDays(last, 16).getTime());
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
    expect(m.viewPreset.base).toBe("weekAndMonth");
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
    expect(chipLabel(c, "200.00", "USD")).toBe("$200");
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

describe("board layout helpers", () => {
  it("chips never truncate mid-number: whole dollars, honest suffixes", () => {
    const c = contributionFor(demoBoard(), 3, "meena");
    expect(chipLabel({ ...c, status: "COMPLETED", gross: "1154.93", order_id: "SBX-1" }, "200.00", "USD")).toBe("$1,155");
    expect(chipLabel({ ...c, status: "PENDING", predicted: true }, "200.00", "USD")).toBe("At risk");
    expect(chipLabel({ ...c, status: "COVERED", label: "covered by buffer, invoice due Fri" }, "200.00", "USD")).toBe("Covered");
  });

  it("monthly chips end before the due date and leave room for the pot label", () => {
    expect(chipWindowDays(30, false)).toBe(18);
    expect(chipWindowDays(7, true)).toBe(4);
    const m = mapBoard(demoBoard())!;
    const c = m.events.find((e) => e.id === "c-2-meena")!;
    const pot = m.events.find((e) => e.id === "pot-2")!;
    expect((pot.startDate.getTime() - c.endDate.getTime()) / DAY_MS).toBe(2);
    expect(m.timeRanges.find((t) => t.id === "now")?.name).toBe("");
  });

  it("focusHandle prefers a moved member, then the recovery member, then the latest decline", () => {
    const board = demoBoard();
    expect(focusHandle(null)).toBeNull();
    expect(focusHandle(board, ["asha"], "ravi")).toBe("asha");
    expect(focusHandle(board, ["ghost"], "ravi")).toBe("ravi");
    expect(focusHandle(board, [], null)).toBe("ravi"); // fixture: ravi declined round 2
    expect(focusHandle({ ...board, contributions: [] }, [], null)).toBeNull();
  });
});
