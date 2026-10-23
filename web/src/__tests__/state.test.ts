import { describe, expect, it } from "vitest";
import { demoBoard } from "./fixtures";
import { applyEvent, initialState, nextRound, reducer, type UiState } from "../state";

function withBoard(): UiState {
  return reducer(initialState, { type: "board", board: demoBoard() });
}

describe("applyEvent", () => {
  it("chip patches an existing contribution and adds a missing one", () => {
    const s = withBoard();
    const patched = applyEvent(s, {
      type: "chip",
      ts: "t",
      round_n: 2,
      member: "ravi",
      status: "COMPLETED",
      order_id: "O2",
      capture_id: "C2",
      webhook_verified: true,
    });
    const ravi = patched.board!.contributions.find((c) => c.round_n === 2 && c.member === "ravi")!;
    expect(ravi.status).toBe("COMPLETED");
    expect(ravi.order_id).toBe("O2");
    expect(ravi.capture_id).toBe("C2");
    expect(ravi.webhook_verified).toBe(true);
    expect(ravi.label).toBe("API decline: sandbox-injected"); // untouched fields are kept

    const added = applyEvent(patched, { type: "chip", ts: "t", round_n: 3, member: "meena", status: "PENDING", predicted: true });
    const meena = added.board!.contributions.find((c) => c.round_n === 3 && c.member === "meena")!;
    expect(meena.predicted).toBe(true);
    expect(added.board!.contributions).toHaveLength(4);
  });

  it("trace appends entries and caps the list", () => {
    let s = withBoard();
    for (let i = 0; i < 505; i += 1) {
      s = applyEvent(s, { type: "trace", ts: "t", kind: "tool_call", text: `call ${i}`, tool: "get_circle", ms: 3 });
    }
    expect(s.trace).toHaveLength(500);
    expect(s.trace[s.trace.length - 1].text).toBe("call 504");
    expect(s.trace[0].tool).toBe("get_circle");
  });

  it("payout marks the round PAID on SUCCESS and keeps the receipt", () => {
    const s = applyEvent(withBoard(), {
      type: "payout",
      ts: "t",
      round_n: 2,
      status: "SUCCESS",
      batch_id: "B2",
      item_id: "I2",
      amount: "385.06",
      recipient: "asha",
      wallet: "VENMO",
    });
    const r = s.board!.rounds.find((r) => r.n === 2)!;
    expect(r.status).toBe("PAID");
    expect(r.payout).toEqual({ batch_id: "B2", item_id: "I2", status: "SUCCESS", amount: "385.06" });
    expect(s.payout?.recipient).toBe("asha");

    const unclaimed = applyEvent(withBoard(), {
      type: "payout",
      ts: "t",
      round_n: 2,
      status: "UNCLAIMED",
      amount: "385.06",
      recipient: "asha",
      wallet: "PAYPAL",
    });
    expect(unclaimed.board!.rounds.find((r) => r.n === 2)!.status).toBe("COLLECTING");
  });

  it("ticker, toast, queue, board and schedule_changed", () => {
    let s = withBoard();
    s = applyEvent(s, { type: "ticker", ts: "t", last_capture_at: "2026-11-20T12:00:00Z", paid_at: null, seconds: null });
    expect(s.ticker.last_capture_at).toBe("2026-11-20T12:00:00Z");
    expect(s.board!.ticker.last_capture_at).toBe("2026-11-20T12:00:00Z");

    s = applyEvent(s, { type: "toast", ts: "t", level: "success", text: "paid" });
    expect(s.toasts[0]).toMatchObject({ level: "success", text: "paid" });

    s = applyEvent(s, { type: "queue", ts: "t", position: 3 });
    expect(s.queuePosition).toBe(3);

    const v = s.boardVersion;
    s = applyEvent(s, { type: "board", ts: "t" });
    expect(s.boardVersion).toBe(v + 1);

    s = applyEvent(s, { type: "schedule_changed", ts: "t", rotation: ["meena", "ravi", "asha"] });
    expect(s.board!.circle.rotation).toEqual(["meena", "ravi", "asha"]);
    expect(s.board!.members.find((m) => m.handle === "ravi")!.rotation_pos).toBe(2);
    expect(s.board!.rounds.find((r) => r.n === 2)!.recipient).toBe("ravi");
    expect(s.board!.rounds.find((r) => r.n === 1)!.recipient).toBe("meena"); // paid rounds keep their recipient
  });

  it("ignores unknown events", () => {
    const s = withBoard();
    expect(applyEvent(s, { type: "unknown", ts: "t", name: "hello", raw: {} })).toBe(s);
  });
});

describe("reducer", () => {
  it("resetRun drops run-scoped state", () => {
    let s = withBoard();
    s = applyEvent(s, { type: "trace", ts: "t", kind: "text", text: "hi" });
    s = reducer(s, { type: "resetRun" });
    expect(s.board).toBeNull();
    expect(s.trace).toEqual([]);
    expect(s.payout).toBeNull();
  });

  it("nextRound is the lowest round that is not PAID or VOID", () => {
    expect(nextRound(demoBoard())).toBe(2);
    expect(nextRound(null)).toBeNull();
  });
});
