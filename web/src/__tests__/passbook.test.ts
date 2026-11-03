import { describe, expect, it } from "vitest";
import { roundLine, tokenFromHash, turnLine, words } from "../passbook";
import { parseRoute } from "../routes";
import type { PassbookView } from "../types";

function view(over: Partial<PassbookView> = {}): PassbookView {
  return {
    ok: true,
    label: "demo member view, no login",
    member: { handle: "tomas", name: "Tomas Alvarez", lang: "en" },
    circle: { name: "Temple Circle A", currency: "USD", amount: "200.00", cadence: "monthly", rounds: 6, charter_sha256: "b85cfed8" },
    payments: [],
    this_round: { round_n: 3, paid: 5, total: 6, covered_by_buffer: 1, mine: "COMPLETED" },
    turn: { original_round: 6, round_n: 5, moved: true, payout_status: null, payout_amount: null, payout_batch_id: null, due: null, expected_pot: "1154.93" },
    fees: { paid: "14.94", per_payment: "7.47", policy: "net_pot" },
    if_someone_misses: { buffer: "250.00", remedy_menu: ["buffer_advance"], quorum: "4 of 6", grace_days: 3, charter_sha256: "b85cfed8" },
    repayment: null,
    expires_at: 0,
    ...over,
  };
}

describe("member passbook", () => {
  it("reads the capability only from the fragment", () => {
    expect(tokenFromHash("#tkc1.abc.def")).toBe("tkc1.abc.def");
    expect(tokenFromHash("#nope")).toBeNull();
    expect(tokenFromHash("")).toBeNull();
    expect(parseRoute("/m")).toEqual({ page: "passbook" });
  });

  it("dignity rule: counts for others, 'you' only for the member's own covered share", () => {
    const other = roundLine(view(), "en");
    expect(other).toEqual(["5 of 6 paid", "1 covered by the circle buffer"]);
    const mine = roundLine(view({ this_round: { round_n: 3, paid: 5, total: 6, covered_by_buffer: 1, mine: "COVERED" } }), "en");
    expect(mine[2]).toBe(words("en").youCovered);
  });

  it("says the turn move in both languages", () => {
    expect(turnLine(view(), "en")).toBe("Round 6 → 5 (a swap you agreed to)");
    expect(turnLine(view(), "hi")).toContain("राउंड 6 → 5");
    expect(turnLine(view({ turn: { ...view().turn, moved: false, round_n: 3 } }), "en")).toBe("You receive the pot in round 3");
  });
});
