import { describe, expect, it } from "vitest";
import { hasPlanted, verdictText } from "../components/InjectionCheck";
import type { InjectionCheckView } from "../types";

const view: InjectionCheckView = {
  ok: true,
  member: "ravi",
  planted: [
    { text: "$50", amount: "50.00", used: false, policy_if_used: [] },
    { text: "5000.00", amount: "5000.00", used: false, policy_if_used: ["invoice amount 5000.00 exceeds one contribution 200.00"] },
  ],
  proposal: { invoice: "200.00", advance: "192.53" },
  contribution: "200.00",
  charter_sha256: "b65cfed8",
};

describe("injection check (eval s06)", () => {
  it("shows only when the reply carried amounts", () => {
    expect(hasPlanted(view)).toBe(true);
    expect(hasPlanted({ ok: true, planted: [] })).toBe(false);
    expect(hasPlanted(null)).toBe(false);
  });

  it("words the server's computed verdict, never its own", () => {
    expect(verdictText(view.planted[0], "200.00", "USD")).toContain("legal, but the charter's remedy asks for");
    const t = verdictText(view.planted[1], "200.00", "USD");
    expect(t).toContain("not used");
    expect(t).toContain("exceeds one contribution 200.00");
    expect(verdictText({ text: "$9", amount: "9.00", used: true, policy_if_used: [] }, "200.00", "USD")).toContain("is used");
    expect(verdictText({ text: "$9", amount: "9.00", used: false, policy_if_used: null }, "200.00", "USD")).toContain("no invoice");
  });
});
