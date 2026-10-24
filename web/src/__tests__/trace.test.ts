import { describe, expect, it } from "vitest";
import { parseResult, resultSummary } from "../trace";

describe("resultSummary", () => {
  it("reads a capture as one sentence", () => {
    const text = JSON.stringify({
      attempt: 1,
      capture_id: "46G1285",
      decline_code: null,
      fee: "7.47",
      gross: "200.00",
      member: "dev",
      net: "192.53",
      ok: true,
      round_n: 3,
      status: "COMPLETED",
    });
    expect(resultSummary(text, "get_order_details")).toBe(
      "get_order_details → dev $200.00 COMPLETED, fee $7.47, net $192.53, round 3",
    );
  });

  it("names a decline and falls back to a short field list", () => {
    expect(resultSummary(JSON.stringify({ member: "ravi", status: "DECLINED", decline_code: "INSTRUMENT_DECLINED" }))).toBe(
      "ravi DECLINED, decline INSTRUMENT_DECLINED",
    );
    expect(resultSummary(JSON.stringify({ flagged: true, count: 2 }))).toBe("flagged true, count 2");
  });

  it("leaves plain text alone", () => {
    expect(parseResult("Round 3 collected")).toBeNull();
    expect(resultSummary("Round 3 collected")).toBeNull();
    expect(resultSummary("{not json")).toBeNull();
  });
});
