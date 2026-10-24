import { describe, expect, it } from "vitest";
import { tickerView } from "../components/Ticker";
import { queueEtaSeconds } from "../components/QueueBadge";
import { isSeededReceipt, receiptFrom, statusTone } from "../components/PayoutReceipt";
import { demoBoard } from "./fixtures";

describe("tickerView", () => {
  const cap = "2026-11-01T10:00:00Z";
  const capMs = Date.parse(cap);
  const opts = { nowMs: capMs + 6_000, running: false, seeded: false };

  it("uses the server seconds once paid and labels seeded history", () => {
    expect(tickerView(null, opts).value).toBe("—");
    const paid = { last_capture_at: cap, paid_at: "2026-11-01T10:00:05Z", seconds: 4.2 };
    expect(tickerView(paid, opts)).toMatchObject({ value: "4.2 s", tone: "done" });
    expect(tickerView({ ...paid, seconds: null }, opts).value).toBe("5.0 s");
    const seeded = tickerView({ ...paid, seconds: 42 }, { ...opts, seeded: true });
    expect(seeded.sub).toBe("seeded history · no PayPal call");
    expect(seeded.announce).toBeNull();
  });

  it("counts from the server capture time only while a payout runs", () => {
    const waiting = { last_capture_at: cap, paid_at: null, seconds: null };
    expect(tickerView(waiting, { ...opts, running: true })).toMatchObject({ value: "6.0 s", tone: "live" });
    // Not running (a failed payout, or simply not started): nothing is counted, however long it waits.
    const idle = tickerView(waiting, { ...opts, nowMs: capMs + 461_900 });
    expect(idle.value).toBe("—");
    expect(idle.sub).toMatch(/^waiting for payout/);
    expect(tickerView({ last_capture_at: null, paid_at: null, seconds: null }, opts).value).toBe("—");
  });
});

describe("queueEtaSeconds", () => {
  it("is about 1.5 s per queued job", () => {
    expect(queueEtaSeconds(0)).toBe(0);
    expect(queueEtaSeconds(4)).toBe(6);
  });
});

describe("PayoutReceipt helpers", () => {
  it("statusTone maps PayPal payout item statuses", () => {
    expect(statusTone("SUCCESS")).toBe("good");
    expect(statusTone("UNCLAIMED")).toBe("warn");
    expect(statusTone("PENDING")).toBe("warn");
    expect(statusTone("FAILED")).toBe("critical");
    expect(statusTone("SOMETHING")).toBe("pending");
  });

  it("receiptFrom prefers the live event and falls back to the latest paid round", () => {
    const rounds = demoBoard().rounds;
    const fromBoard = receiptFrom(null, rounds);
    expect(fromBoard).toMatchObject({ roundN: 1, status: "SUCCESS", batchId: "REPLAY-BATCH-1" });
    const live = receiptFrom(
      { type: "payout", ts: "t", round_n: 2, status: "UNCLAIMED", amount: "385.06", recipient: "asha", wallet: "VENMO" },
      rounds,
    );
    expect(live).toMatchObject({ roundN: 2, status: "UNCLAIMED", wallet: "VENMO", batchId: null });
  });
});

describe("seeded receipts", () => {
  it("flags fixture REPLAY- payouts, not sandbox ones", () => {
    expect(isSeededReceipt({ itemId: "REPLAY-ITEM-2", batchId: "REPLAY-BATCH-2" })).toBe(true);
    expect(isSeededReceipt({ itemId: "8XK2", batchId: "5UB3" })).toBe(false);
    expect(isSeededReceipt(null)).toBe(false);
  });
});
