import { describe, expect, it } from "vitest";
import {
  billingPlanLines,
  charterRows,
  compileModeChip,
  feeLine,
  fieldProblems,
  generalProblems,
  shortHash,
} from "../compile";
import { isPayPalApproveUrl, joinStatusView, statusForLoadError, walletEmailProblem } from "../join";
import { parseRoute, routePath } from "../routes";
import type { BillingPlan, CharterJson, JoinCard } from "../types";

const CHARTER: CharterJson = {
  name: "Temple Circle A",
  currency: "USD",
  amount: "200.00",
  cadence: "monthly",
  start_date: "2026-11-01",
  members: [
    { handle: "meena", name: "Meena Iyer", lang: "en", payday_rule: null, payout_wallet: "PAYPAL", track_record_rounds: 6 },
    { handle: "ravi", name: "Ravi Sharma", lang: "hi", payday_rule: "every 2nd Friday", payout_wallet: "PAYPAL", track_record_rounds: 0 },
  ],
  rotation: ["meena", "ravi"],
  fee_policy: "net_pot",
  buffer: { seed: "250.00", source: "organizer", retention_pct: 0 },
  grace_days: 3,
  quorum: "2/2",
  remedy_menu: ["retry_on_date", "buffer_advance"],
  languages: ["en", "hi"],
};

const PLAN: BillingPlan = {
  name: "Temple Circle A",
  billing_cycles: [
    {
      tenure_type: "REGULAR",
      sequence: 1,
      total_cycles: 6,
      pricing_scheme: { pricing_model: "VARIABLE", price: { value: "200.00", currency_code: "USD" } },
      frequency: { interval_unit: "MONTH", interval_count: 1 },
      start_date: "2026-11-01",
    },
  ],
};

describe("charter compiler mappers", () => {
  it("builds one row per field with explanations and field problems", () => {
    const rows = charterRows({
      charter: CHARTER,
      explanations: { amount: 'Rules parser read: "$200"' },
      problems: ["rotation: collateral rule - moved ravi", "grace_days: not stated; using 3", "something general"],
    });
    expect(rows.map((r) => r.field)).toContain("remedy_menu");
    const amount = rows.find((r) => r.field === "amount")!;
    expect(amount.value).toBe("$200.00 per member");
    expect(amount.explanation).toContain("$200");
    expect(rows.find((r) => r.field === "rotation")!.value).toBe("Meena → Ravi");
    expect(rows.find((r) => r.field === "rotation")!.problems).toEqual(["collateral rule - moved ravi"]);
    expect(rows.find((r) => r.field === "members")!.value).toBe("Meena (6 rounds before), Ravi (new)");
    expect(rows.find((r) => r.field === "quorum")!.value).toBe("2 of 2 votes for big decisions");
    expect(rows.find((r) => r.field === "languages")!.value).toBe("English, Hindi");
    expect(charterRows({ charter: null, explanations: {}, problems: [] })).toEqual([]);
  });

  it("shows a repeated source quote once", () => {
    const quote = 'Rules parser read: "every member pays $200 each month"';
    const rows = charterRows({ charter: CHARTER, explanations: { amount: quote, cadence: quote }, problems: [] });
    expect(rows.find((r) => r.field === "amount")!.explanation).toBe(quote);
    const cadence = rows.find((r) => r.field === "cadence")!;
    expect(cadence.explanation).toBe("same source line as above");
    expect(cadence.fullExplanation).toBe(quote);
  });

  it("splits field problems from general ones", () => {
    const problems = ["amount: no positive contribution amount found ('x')", "the model said hi"];
    expect(fieldProblems("amount", problems)).toEqual(["no positive contribution amount found ('x')"]);
    expect(generalProblems(problems)).toEqual(["the model said hi"]);
  });

  it("writes the fee line and the billing plan PayPal shows", () => {
    expect(feeLine({ fee_policy: "net_pot", fee_estimate: "7.47" })).toBe("Fees ≈ $7.47 per contribution; pot paid net");
    expect(feeLine({ fee_policy: "gross_up", fee_estimate: "7.47" })).toMatch(/members cover them/);
    expect(feeLine(null)).toBe("");
    expect(billingPlanLines(PLAN)).toEqual(["6 × $200.00 every month from Nov 1, 2026 · variable amount"]);
    expect(billingPlanLines(null)).toEqual([]);
  });

  it("labels who produced the draft", () => {
    expect(compileModeChip({ mode: "rules", source: "parser", mode_label: "rules mode: parser" }).cls).toBe("tk-chip-pending");
    expect(compileModeChip({ mode: "rules", source: "template", mode_label: "rules mode: template" }).cls).toBe("tk-chip-warn");
    expect(compileModeChip({ mode: "replay", source: "model", mode_label: "AI replay (Gemini, recorded)" }).label).toBe("AI replay (Gemini, recorded)");
    expect(compileModeChip({ mode: "live", source: "model", mode_label: "" }).label).toBe("live");
    expect(shortHash("b85cfed8ada903122d41")).toBe("b85cfed8ada9");
  });
});

function card(status: string, extra: Partial<JoinCard> = {}): JoinCard {
  return {
    ok: true,
    circle: {
      id: "abc123",
      name: "Temple Circle A",
      amount: "200.00",
      currency: "USD",
      cadence: "monthly",
      cycles: 6,
      start_date: "2026-11-01",
      fee_policy: "net_pot",
      fee_note: "PayPal's fee ...",
      fees: { fee_policy: "net_pot", fee_estimate: "7.47", net_per_contribution: "192.53", gross_pot: "1200.00", pot_estimate: "1154.93", payout_fee_reserve: "0.25", line: "..." },
      charter_sha256: "0".repeat(64),
      buffer_seed: "250.00",
      quorum: "4/6",
      grace_days: 3,
      remedy_menu: [],
      members: [],
      billing_plan: PLAN,
      kind: "fixture",
      status: "active",
    },
    member: { handle: "ravi", name: "Ravi Sharma", lang: "hi" },
    vaulted: status === "vaulted",
    join: {
      handle: "ravi",
      name: "Ravi Sharma",
      lang: "hi",
      payout_round: 5,
      payout_date: "2027-03-01",
      payout_wallet: "PAYPAL",
      payout_receiver_masked: null,
      join_status: status,
      join_message: `server says ${status}`,
    },
    invite: { expires_at: "2026-10-30T00:00:00Z" },
    paypal_ready: true,
    consent: "You approve ...",
    ...extra,
  };
}

describe("join page states", () => {
  it("shows the stored status with the server's message", () => {
    const v = joinStatusView(card("invited"), null);
    expect(v.status).toBe("invited");
    expect(v.body).toBe("server says invited");
    expect(v.canContinue).toBe(true);
    expect(v.showWallet).toBe(false);
  });

  it("prefers the PayPal return status and falls back to known copy", () => {
    const v = joinStatusView(card("invited"), "cancelled");
    expect(v.status).toBe("cancelled");
    expect(v.tone).toBe("warn");
    expect(v.body).toMatch(/Nothing was saved/);
    expect(v.canContinue).toBe(true);
    expect(joinStatusView(card("declined"), "declined").body).toBe("server says declined");
    expect(joinStatusView(card("error"), "error").tone).toBe("critical");
    expect(joinStatusView(card("invited"), "bogus").status).toBe("invited");
  });

  it("success: vaulted wins over a stale status and offers the wallet", () => {
    const v = joinStatusView(card("vaulted"), "cancelled");
    expect(v.status).toBe("vaulted");
    expect(v.tone).toBe("good");
    expect(v.canContinue).toBe(false);
    expect(v.showWallet).toBe(true);
  });

  it("load errors and closed circles", () => {
    expect(statusForLoadError(410)).toBe("expired");
    expect(statusForLoadError(404)).toBe("invalid");
    expect(statusForLoadError(500)).toBe("error");
    const expired = joinStatusView(null, "expired");
    expect(expired.canContinue).toBe(false);
    expect(expired.body).toMatch(/expired/);
    const closed = card("invited");
    closed.circle.status = "closed";
    expect(joinStatusView(closed, null).canContinue).toBe(false);
  });

  it("validates the wallet email like the server and only trusts PayPal approve URLs", () => {
    expect(walletEmailProblem("", true)).toBeNull();
    expect(walletEmailProblem("", false)).toMatch(/Enter/);
    expect(walletEmailProblem("nope", true)).toMatch(/email/);
    expect(walletEmailProblem("member@example.com", false)).toBeNull();
    expect(isPayPalApproveUrl("https://www.sandbox.paypal.com/agreements/approve?approval_session_id=X")).toBe(true);
    expect(isPayPalApproveUrl("https://paypal.com.evil.example/x")).toBe(false);
    expect(isPayPalApproveUrl("http://www.sandbox.paypal.com/x")).toBe(false);
    expect(isPayPalApproveUrl(null)).toBe(false);
  });
});

describe("routes", () => {
  it("parses board, compile and join paths", () => {
    expect(parseRoute("/")).toEqual({ page: "board" });
    expect(parseRoute("/compile/")).toEqual({ page: "compile" });
    expect(parseRoute("/join/abc123.ravi.k1.sig", "?status=vaulted")).toEqual({ page: "join", invite: "abc123.ravi.k1.sig", status: "vaulted" });
    expect(parseRoute("/join/abc123.ravi.k1.sig")).toEqual({ page: "join", invite: "abc123.ravi.k1.sig", status: null });
    // the server's own return route and junk fall back to the board
    expect(parseRoute("/join/x/return")).toEqual({ page: "board" });
    expect(parseRoute("/join/%E0%A4%A")).toEqual({ page: "board" });
    expect(parseRoute("/nope")).toEqual({ page: "board" });
  });

  it("round-trips paths", () => {
    expect(routePath({ page: "join", invite: "a.b.c.d", status: "cancelled" })).toBe("/join/a.b.c.d?status=cancelled");
    expect(routePath({ page: "compile" })).toBe("/compile");
    expect(routePath({ page: "board" })).toBe("/");
  });
});
