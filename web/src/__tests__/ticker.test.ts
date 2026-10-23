import { describe, expect, it } from "vitest";
import { tickerSeconds } from "../components/Ticker";
import { queueEtaSeconds } from "../components/QueueBadge";
import { receiptFrom, statusTone } from "../components/PayoutReceipt";
import { demoBoard } from "./fixtures";

describe("tickerSeconds", () => {
  it("uses the server seconds once paid, counts up while waiting", () => {
    expect(tickerSeconds(null, 0, null)).toBeNull();
    expect(tickerSeconds({ last_capture_at: "a", paid_at: "b", seconds: 4.2 }, 0, null)).toBe(4.2);
    expect(
      tickerSeconds({ last_capture_at: "2026-11-01T10:00:00Z", paid_at: "2026-11-01T10:00:05Z", seconds: null }, 0, null),
    ).toBe(5);
    expect(tickerSeconds({ last_capture_at: "x", paid_at: null, seconds: null }, 10_000, 4_000)).toBe(6);
    expect(tickerSeconds({ last_capture_at: null, paid_at: null, seconds: null }, 0, null)).toBeNull();
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
