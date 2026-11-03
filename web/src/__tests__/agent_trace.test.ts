import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AgentTrace } from "../components/AgentTrace";
import type { TraceEntry } from "../state";
import { declineDebugId, modeLabel, paypalObjects, recordedDay, TOOL_WHY } from "../trace";

function entry(over: Partial<TraceEntry>): TraceEntry {
  return { id: 1, ts: "2026-11-03T09:00:00Z", kind: "tool_call", text: "", ...over };
}

function render(entries: TraceEntry[]): string {
  return renderToStaticMarkup(createElement(AgentTrace, { entries, mode: "replay", keyConfigured: false, onClear: () => {} }));
}

describe("trace row labels", () => {
  it("says the mode, the model and the day a replayed turn was recorded", () => {
    expect(recordedDay("2026-11-03")).toBe("3 Nov");
    expect(recordedDay("3 Nov")).toBeNull();
    expect(modeLabel("AI replay", "replay", "gemini-3.5-flash-lite", "2026-11-03")).toBe(
      "AI replay · gemini-3.5-flash-lite · recorded 3 Nov",
    );
    expect(modeLabel("live AI", "live", "gemini-3.5-flash-lite", "2026-11-03")).toBe("live AI · gemini-3.5-flash-lite");
    expect(modeLabel("rules mode", "rules", "gemini-3.5-flash-lite")).toBe("rules mode");
  });

  it("names the PayPal objects a result produced, and the debug id only on a decline", () => {
    const invoice = JSON.stringify({ ok: true, results: [{ invoice_id: "INV2-AB12-CD34", qr: true }] });
    expect(paypalObjects(invoice)).toBe("invoice INV2-AB12-CD34 + QR");
    const capture = JSON.stringify({ order_id: "5O190127", capture_id: "3C679366", status: "COMPLETED", debug_id: "f00d" });
    expect(paypalObjects(capture)).toBe("order 5O190127 · capture 3C679366");
    expect(declineDebugId(capture)).toBeNull();
    expect(declineDebugId(JSON.stringify({ decline_code: "INSTRUMENT_DECLINED", debug_id: "f00d" }))).toBe("f00d");
    expect(paypalObjects(JSON.stringify({ round_n: 3 }))).toBeNull();
    expect(paypalObjects("not json")).toBeNull();
  });

  it("renders the row: label, tool, reason and PayPal object", () => {
    const html = render([
      entry({ tool: "propose_remedies", mode: "replay", model: "gemini-3.5-flash-lite", recorded: "2026-11-03", reason: "Ravi's payday is Friday." }),
      entry({ id: 2, kind: "tool_result", tool: "execute_remedies", text: JSON.stringify({ invoice_id: "INV2-X", qr: true }) }),
      entry({ id: 3, tool: "get_round_matrix", mode: "rules" }),
    ]);
    expect(html).toContain("AI replay · gemini-3.5-flash-lite · recorded 3 Nov");
    expect(html).toContain("propose_remedies");
    expect(html).toContain("Ravi&#x27;s payday is Friday.");
    expect(html).toContain("PayPal: invoice INV2-X + QR");
    expect(html).toContain(TOOL_WHY.get_round_matrix); // rules mode: the tool's purpose stands in for a reason
  });

  it("renders a hostile reason as text, never as HTML", () => {
    const html = render([entry({ tool: "record_note", mode: "replay", model: "m", reason: '<script>alert("x")</script><img src=x onerror=alert(1)>' })]);
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;");
  });
});
