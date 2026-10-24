import { describe, expect, it } from "vitest";
import { fmtDateTime, fmtMoney } from "../format";

describe("fmtMoney / fmtDateTime", () => {
  it("adds thousands separators and can drop cents for chips", () => {
    expect(fmtMoney("1154.93")).toBe("$1,154.93");
    expect(fmtMoney("1154.93", "USD", { cents: false })).toBe("$1,155");
    expect(fmtMoney(-7.5)).toBe("-$7.50");
    expect(fmtMoney("200", "EUR")).toBe("200.00 EUR");
    expect(fmtMoney(null)).toBe("—");
    expect(fmtMoney("n/a")).toBe("$n/a");
  });

  it("shows clocks without seconds", () => {
    const s = fmtDateTime("2026-12-29T14:30:00Z");
    expect(s).toMatch(/2026/);
    expect(s).not.toMatch(/:\d{2}:\d{2}/);
    expect(fmtDateTime(null)).toBe("—");
  });
});
