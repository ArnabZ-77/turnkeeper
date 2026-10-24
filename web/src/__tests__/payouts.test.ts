import { describe, expect, it } from "vitest";
import { demoBoard } from "./fixtures";
import { CANCEL_UNAVAILABLE, applyRerouteToRound, balanceChip, unclaimedView } from "../payouts";
import { initialState, reducer } from "../state";
import type { PayoutAttemptInfo, RoundInfo } from "../types";

function attempt(rev: number, extra: Partial<PayoutAttemptInfo> = {}): PayoutAttemptInfo {
  return {
    rev,
    batch_id: `BATCH-${rev}`,
    item_id: `ITEM-${rev}`,
    status: "SUCCESS",
    amount: "1154.93",
    wallet: "PAYPAL",
    receiver_kind: "payout_receiver",
    funding: "ledger",
    cancel_status: null,
    return_status: null,
    label: null,
    ...extra,
  };
}

function round(extra: Partial<RoundInfo> = {}): RoundInfo {
  return {
    n: 3,
    due_at: "2027-01-01",
    recipient: "dev",
    status: "COLLECTED",
    payout: { batch_id: "BATCH-1", item_id: "ITEM-1", status: "UNCLAIMED", amount: "1154.93" },
    paid_at: null,
    payout_rev: 1,
    payout_attempts: [attempt(1, { status: "UNCLAIMED", receiver_kind: "unclaimed_test" })],
    pending_return: null,
    ...extra,
  };
}

describe("balanceChip", () => {
  it("is null before any balance check", () => {
    expect(balanceChip(null)).toBeNull();
    expect(balanceChip({ value: null, last_refreshed: null, stale: null, checked_at: null })).toBeNull();
  });

  it("reads 'PayPal balance $X (refreshed hh:mm)' and flags stale readings", () => {
    const fresh = balanceChip({ value: "5000.00", last_refreshed: "2026-10-23T10:05:00Z", stale: false, checked_at: "2026-10-23T10:06:00Z" })!;
    expect(fresh.label).toMatch(/^PayPal balance \$5,000\.00 \(refreshed \d{2}:\d{2}\)$/);
    expect(fresh.tip).toMatch(/ledger/);
    const stale = balanceChip({ value: "5000.00", last_refreshed: "2026-10-23T07:00:00Z", stale: true, checked_at: null })!;
    expect(stale.label).toContain("stale");
    expect(stale.cls).toBe("tk-chip-warn");
    expect(balanceChip({ value: "1.00", last_refreshed: null, stale: null, checked_at: null })!.label).toContain("refresh time unknown");
  });
});

describe("unclaimedView", () => {
  it("offers Re-route while the current item is UNCLAIMED", () => {
    const v = unclaimedView(round());
    expect(v.canReroute).toBe(true);
    expect(v.rerouted).toBe(false);
    expect(v.chips.map((c) => c.label)).toEqual(["UNCLAIMED"]);
  });

  it("shows cancel-unavailable, pending return and the re-routed batch", () => {
    const v = unclaimedView(
      round({
        payout: { batch_id: "BATCH-2", item_id: "ITEM-2", status: "SUCCESS", amount: "1154.93" },
        status: "PAID",
        payout_rev: 2,
        payout_attempts: [
          attempt(2),
          attempt(1, { status: "UNCLAIMED", cancel_status: "cancel_failed", return_status: "pending_return", funding: "fronted_pending_return" }),
        ],
        pending_return: { amount: "1154.93", items: [{ rev: 1, item_id: "ITEM-1", amount: "1154.93" }], label: "pending return" },
      }),
    );
    expect(v.canReroute).toBe(false);
    expect(v.rerouted).toBe(true);
    expect(v.chips.map((c) => c.label)).toEqual(["re-routed (r2)", CANCEL_UNAVAILABLE, "pending return $1,154.93"]);
    expect(v.attempts.map((a) => a.rev)).toEqual([1, 2]);
    expect(v.attempts[0].superseded).toBe(true);
    expect(v.attempts[0].notes).toEqual([CANCEL_UNAVAILABLE, "pending return"]);
    expect(v.attempts[1].notes).toEqual(["re-routed"]);
  });

  it("handles a round with no attempts", () => {
    const v = unclaimedView({ ...round(), payout: null, payout_attempts: undefined, payout_rev: undefined });
    expect(v.canReroute).toBe(false);
    expect(v.chips).toEqual([]);
    expect(v.attempts).toEqual([]);
  });
});

describe("re-route payout events", () => {
  it("records the new revision and the pending return on the round", () => {
    const r = applyRerouteToRound(round(), {
      type: "payout",
      ts: "t",
      round_n: 3,
      status: "SUCCESS",
      amount: "1154.93",
      recipient: "dev",
      wallet: "PAYPAL",
      rev: 2,
      cancel_status: "cancel_failed",
      pending_return: { amount: "1154.93", item_id: "ITEM-1", label: "pending return" },
    });
    expect(r.payout_rev).toBe(2);
    expect(r.pending_return!.items[0]).toEqual({ rev: 1, item_id: "ITEM-1", amount: "1154.93" });

    // A first payout (no rev) leaves the round's re-route fields alone.
    const plain = applyRerouteToRound(round(), { type: "payout", ts: "t", round_n: 3, status: "SUCCESS", amount: "1", recipient: "dev", wallet: "PAYPAL" });
    expect(plain.payout_rev).toBe(1);
  });

  it("goes through the reducer's payout case", () => {
    let s = reducer(initialState, { type: "board", board: demoBoard() });
    s = reducer(s, {
      type: "event",
      event: { type: "payout", ts: "t", round_n: 2, status: "SUCCESS", amount: "385.06", recipient: "asha", wallet: "PAYPAL", rev: 2, pending_return: null },
    });
    const r2 = s.board!.rounds.find((r) => r.n === 2)!;
    expect(r2.status).toBe("PAID");
    expect(r2.payout_rev).toBe(2);
  });
});
