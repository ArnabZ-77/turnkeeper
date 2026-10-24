import { describe, expect, it } from "vitest";
import { demoBoard } from "./fixtures";
import { applyEvent, initialState, mergeRecovery, reducer, type UiState } from "../state";
import { mapBoard } from "../board/mapping";
import {
  SIMULATE_DECLINE_LABEL,
  canChargeLive,
  consentRows,
  declinedContribution,
  modeBadge,
  modeChip,
  movedHandles,
  paypalChip,
  recoveryButtons,
  remedyHeadline,
  remedyProblems,
  setProblems,
  sortedInbox,
  subtitleFor,
  forecastTranslations,
  voteBar,
} from "../recovery";
import type { InboxMessage, RecoveryInfo, RemedyInfo } from "../types";

function remedy(type: string, params: Partial<RemedyInfo["params"]> = {}, status = "proposed"): RemedyInfo {
  return {
    id: `rm_${type}`,
    type,
    member: "ravi",
    round_n: 2,
    params: { swap_with: null, amount: null, due_date: null, ...params },
    reason: `why ${type}`,
    status,
    label: null,
  };
}

function rec(overrides: Partial<RecoveryInfo> = {}): RecoveryInfo {
  return {
    status: "voting",
    set_id: "rs_test0001",
    member: "ravi",
    round_n: 2,
    source: "rules",
    commitment: { member: "ravi", amount: "200.00", date: "2026-11-27", source: "rules" },
    remedies: [
      remedy("buffer_advance", { amount: "192.53" }),
      remedy("repay_by_invoice", { amount: "200.00", due_date: "2026-11-27" }),
      remedy("turn_swap", { swap_with: "asha" }),
    ],
    problems: [],
    votes: { yes: 1, no: 0, needed: 4, total: 6, passed: false, closes_at: "2026-11-22T12:00:00Z" },
    consents: { needed: ["ravi", "asha"], given: ["ravi"], refused: [] },
    approval: { by: "priya", status: "requested", at: null },
    blockers: ["1/4 yes votes", "consent missing from asha", "organizer approval missing"],
    invoice: null,
    ...overrides,
  };
}

function msg(id: number, text: string, extra: Partial<InboxMessage> = {}): InboxMessage {
  return { id, member: "ravi", lang: "en", text, direction: "out", source: "rules", round_n: 2, ts: null, ...extra };
}

function withBoard(): UiState {
  return reducer(initialState, { type: "board", board: demoBoard() });
}

describe("run mode labels", () => {
  it("maps each mode to a chip and never defaults to live", () => {
    expect(modeChip("live")?.label).toBe("live AI");
    expect(modeChip("replay")?.label).toBe("AI replay");
    expect(modeChip("rules")?.label).toBe("rules mode");
    expect(modeChip("engine")?.label).toBe("engine (no agent)");
    expect(modeChip(undefined)).toBeNull();
    expect(modeChip("something")).toBeNull();
    expect(modeBadge("replay")).toBe("replay");
    expect(modeBadge(null)).toBeNull();
  });

  it("PayPal chip says 'not configured' (never 'replay') without credentials", () => {
    expect(paypalChip({ paypal_live: true })).toMatchObject({ label: "PayPal sandbox", cls: "tk-chip-good" });
    expect(paypalChip({ paypal_live: false })).toMatchObject({ label: "PayPal not configured", cls: "tk-chip-warn" });
  });
});

describe("remedies", () => {
  it("assigns problems to the remedy they concern and keeps set-level ones apart", () => {
    const problems = [
      "buffer advance 300.00 exceeds the buffer balance 250.00",
      "invoice amount 300.00 exceeds one contribution 200.00",
      "turn_swap: unknown swap partner 'zed'",
      "circle is frozen: no remedy can run until it is resumed",
    ];
    const r = rec({ problems }).remedies;
    expect(remedyProblems(r[0], problems)).toEqual([problems[0]]);
    expect(remedyProblems(r[1], problems)).toEqual([problems[1]]);
    expect(remedyProblems(r[2], problems)).toEqual([problems[2]]);
    expect(setProblems(r, problems)).toEqual([problems[3]]);
    expect(remedyProblems(r[0], [])).toEqual([]);
  });

  it("headlines name the amount, date and swap partner", () => {
    const board = demoBoard();
    const [adv, inv, swap] = rec().remedies;
    expect(remedyHeadline(adv, board)).toBe("Buffer advance · $192.53 now");
    expect(remedyHeadline(inv, board)).toContain("Repay by invoice · $200.00 due Nov 27, 2026");
    expect(remedyHeadline(swap, board)).toBe("Turn swap · Ravi ↔ Asha");
  });
});

describe("votes and consents", () => {
  it("vote bar measures yes votes against the quorum", () => {
    expect(voteBar(rec())).toMatchObject({ yes: 1, needed: 4, pct: 25, passed: false });
    expect(voteBar(rec({ votes: { yes: 5, no: 0, needed: 4, total: 6, passed: true, closes_at: null } }))?.pct).toBe(100);
    expect(voteBar(rec({ votes: null }))).toBeNull();
  });

  it("consent rows mark given, refused and pending", () => {
    expect(consentRows(rec())).toEqual([
      { member: "ravi", state: "given" },
      { member: "asha", state: "pending" },
    ]);
    expect(consentRows(rec({ consents: { needed: ["ravi"], given: [], refused: ["ravi"] } }))[0].state).toBe("refused");
  });

  it("judge buttons follow the story", () => {
    const board = demoBoard(); // ravi DECLINED in round 2
    expect(declinedContribution(board)).toEqual({ round_n: 2, member: "ravi" });

    const none = recoveryButtons(board, null);
    expect(none.start).toBeNull();
    expect(none.vote).not.toBeNull();
    expect(none.markPaid).not.toBeNull();

    const open = recoveryButtons(board, rec());
    expect(open).toMatchObject({ start: null, consent: null, vote: null, approve: "Waiting for quorum (1/4)" });
    expect(open.markPaid).toContain("No invoice");

    const voted = recoveryButtons(
      board,
      rec({
        votes: { yes: 4, no: 0, needed: 4, total: 6, passed: true, closes_at: null },
        consents: { needed: ["ravi", "asha"], given: ["ravi", "asha"], refused: [] },
      }),
    );
    expect(voted.vote).toBe("Quorum already reached");
    expect(voted.approve).toBeNull();
    const noConsent = recoveryButtons(
      board,
      rec({ votes: { yes: 4, no: 0, needed: 4, total: 6, passed: true, closes_at: null } }),
    );
    expect(noConsent.approve).toBe("Waiting for consent (1/2)");
    expect(voted.consent).toBe("Every affected member has answered");

    const noSwap = recoveryButtons(board, rec({ consents: { needed: [], given: [], refused: [] } }));
    expect(noSwap.consent).toContain("no consent is needed");

    const invoice = { id: "SIM-INV-1", status: "SENT", member: "ravi", round_n: 2, amount: "200.00", due_date: "2026-11-27", simulated: true, qr: null };
    const executed = recoveryButtons(board, rec({ status: "executed", invoice }));
    expect(executed.approve).toBe("Already executed");
    expect(executed.markPaid).toBeNull();
    expect(recoveryButtons(board, rec({ status: "repaid", invoice: { ...invoice, status: "PAID" } })).markPaid).toBe(
      "Invoice already paid",
    );

    const settled = demoBoard();
    settled.contributions = settled.contributions.map((c) => (c.status === "DECLINED" ? { ...c, status: "COVERED" } : c));
    expect(recoveryButtons(settled, null).start).toContain("No declined contribution");
  });
});

describe("inbox", () => {
  it("subtitles the known Hindi texts and is honest about unknown ones", () => {
    expect(subtitleFor(msg(1, "शुक्रवार को सैलरी आएगी, तब भर दूंगा", { lang: "hi" }))).toBe(
      "Salary comes on Friday, I will pay then.",
    );
    expect(
      subtitleFor(msg(2, "नमस्ते Ravi, राउंड 3 का आपका $200.00 का भुगतान नहीं हो पाया। आप कब भर पाएंगे?", { lang: "hi" })),
    ).toBe("Hi Ravi, your round 3 contribution of $200.00 did not go through. When can you pay?");
    expect(
      subtitleFor(msg(3, "नमस्ते Ravi, सर्कल ने आपका हिस्सा भर दिया है। $200.00 का PayPal इनवॉइस, देय तिथि 2026-11-27।", { lang: "hi" })),
    ).toContain("PayPal invoice for $200.00, due 2026-11-27");
    expect(subtitleFor(msg(4, "मैं कल भरूंगा", { lang: "hi" }))).toBe("Hindi message");
    expect(subtitleFor(msg(4, "मैं कल भरूंगा", { lang: "hi" }), { 4: "I will pay tomorrow." })).toBe("I will pay tomorrow.");
    expect(subtitleFor(msg(5, "Paying Friday"))).toBeNull();
  });

  it("sorts oldest first with ids breaking ties", () => {
    const out = sortedInbox([
      msg(3, "c", { ts: "2026-11-20T12:00:02Z" }),
      msg(2, "b", { ts: "2026-11-20T12:00:01Z" }),
      msg(1, "a", { ts: "2026-11-20T12:00:01Z" }),
    ]);
    expect(out.map((m) => m.id)).toEqual([1, 2, 3]);
  });
});

describe("reducer: recovery cases", () => {
  it("trace lines carry their mode and set lastMode; a finished task overrides it", () => {
    let s = withBoard();
    s = applyEvent(s, { type: "trace", ts: "t", kind: "text", text: "hi" });
    expect(s.lastMode).toBeNull();
    s = applyEvent(s, { type: "trace", ts: "t", kind: "tool_call", text: "", tool: "collect_round", mode: "replay" });
    expect(s.trace[s.trace.length - 1].mode).toBe("replay");
    expect(s.lastMode).toBe("replay");
    s = applyEvent(s, { type: "trace", ts: "t", kind: "system", text: "no mode here" });
    expect(s.lastMode).toBe("replay");
    s = applyEvent(s, { type: "trace", ts: "t", kind: "system", text: "x", mode: "bogus" });
    expect(s.trace[s.trace.length - 1].mode).toBeUndefined();
    s = reducer(s, { type: "runMode", mode: "rules" });
    expect(s.lastMode).toBe("rules");
    expect(reducer(s, { type: "runMode", mode: null })).toBe(s);
  });

  it("schedule_changed records which turns moved; clearMoved drops it", () => {
    let s = withBoard(); // rotation meena, asha, ravi
    s = applyEvent(s, { type: "schedule_changed", ts: "t", rotation: ["meena", "ravi", "asha"] });
    expect(s.moved?.handles.sort()).toEqual(["asha", "ravi"]);
    expect(s.moved?.seq).toBe(1);
    s = reducer(s, { type: "clearMoved" });
    expect(s.moved).toBeNull();
    expect(movedHandles(["a", "b"], ["a", "b"])).toEqual([]);
    expect(movedHandles(null, ["a"])).toEqual([]);
  });

  it("recovery, translations and resetRun", () => {
    let s = withBoard();
    s = reducer(s, { type: "recovery", recovery: rec({ inbox: [msg(1, "hello")] }) });
    expect(s.recovery?.set_id).toBe("rs_test0001");
    s = reducer(s, { type: "translation", id: 7, text: "Salary comes on Friday" });
    expect(s.translations[7]).toBe("Salary comes on Friday");

    // A board refetch carries the recovery block without inbox/QR data: keep those.
    const withQr = rec({
      inbox: [msg(1, "hello")],
      invoice: { id: "INV-1", status: "SENT", member: "ravi", round_n: 2, amount: "200.00", due_date: null, simulated: false, qr: "/qr", qr_data: "data:image/png;base64,AAAA" },
    });
    s = reducer(s, { type: "recovery", recovery: withQr });
    const board = { ...demoBoard(), recovery: rec({ status: "executed", invoice: { ...withQr.invoice!, qr_data: undefined } }) };
    s = reducer(s, { type: "board", board });
    expect(s.recovery?.status).toBe("executed");
    expect(s.recovery?.inbox).toHaveLength(1);
    expect(s.recovery?.invoice?.qr_data).toBe("data:image/png;base64,AAAA");

    s = reducer(s, { type: "resetRun" });
    expect(s.recovery).toBeNull();
    expect(s.lastMode).toBeNull();
    expect(s.translations).toEqual({});
    expect(s.moved).toBeNull();
  });

  it("mergeRecovery prefers the newer block and tolerates missing ones", () => {
    const a = rec({ inbox: [msg(1, "x")] });
    expect(mergeRecovery(null, undefined)).toBeNull();
    expect(mergeRecovery(a, undefined)).toBe(a);
    expect(mergeRecovery(null, a)).toBe(a);
    const b = mergeRecovery(a, rec({ set_id: "rs_other", status: "proposed" }))!;
    expect(b.status).toBe("proposed");
    expect(b.invoice).toBeNull();
  });
});

describe("mapping: recovery additions", () => {
  it("carries member labels and the moved flag onto board resources", () => {
    const board = demoBoard();
    board.members = board.members.map((m) =>
      m.handle === "ravi" ? { ...m, label: "Round 2: covered by buffer · Invoice due Fri" } : m,
    );
    const m = mapBoard(board, ["ravi"])!;
    const ravi = m.resources.find((r) => r.id === "ravi")!;
    expect(ravi.label).toBe("Round 2: covered by buffer · Invoice due Fri");
    expect(ravi.moved).toBe(true);
    expect(m.resources.find((r) => r.id === "meena")!.moved).toBe(false);
    expect(m.resources.find((r) => r.id === "meena")!.label).toBeNull();
  });

  it("moves the pot milestone to the new recipient after a swap", () => {
    let s = withBoard();
    s = applyEvent(s, { type: "schedule_changed", ts: "t", rotation: ["meena", "ravi", "asha"] });
    const m = mapBoard(s.board, s.moved?.handles)!;
    expect(m.resources.map((r) => r.id)).toEqual(["meena", "ravi", "asha"]);
    expect(m.events.find((e) => e.id === "pot-2")!.resourceId).toBe("ravi");
    expect(m.events.find((e) => e.id === "pot-3")!.resourceId).toBe("asha");
  });
});

describe("review fixes (fixer)", () => {
  it("offers consent and vote only while the set is voting", () => {
    const board = demoBoard();
    const proposed = recoveryButtons(board, rec({ status: "proposed" }));
    expect(proposed.consent).toBe("The vote is not open yet");
    expect(proposed.vote).toBe("The vote is not open yet");
    expect(proposed.approve).toBe("Waiting for the vote to open"); // the server would refuse it
    expect(recoveryButtons(board, rec({ status: "approved" })).approve).toBeNull();
    const approved = recoveryButtons(board, rec({ status: "approved" }));
    expect(approved.vote).toBe("Remedy set is approved");
    expect(recoveryButtons(board, rec({ status: "voting" })).vote).toBeNull();
  });

  it("enables 'Mark invoice paid' only for a SENT invoice", () => {
    const board = demoBoard();
    const invoice = { id: "INV2-X", status: "DRAFT", member: "ravi", round_n: 2, amount: "200.00", due_date: null, simulated: false, qr: null };
    expect(recoveryButtons(board, rec({ status: "failed", invoice })).markPaid).toBe("Invoice was not sent (DRAFT)");
    expect(recoveryButtons(board, rec({ status: "executed", invoice: { ...invoice, status: "SENT" } })).markPaid).toBeNull();
  });

  it("swaps Run Round 3 for the simulated decline when the sandbox cannot charge", () => {
    expect(canChargeLive({ paypal_live: true, vault_seeded: true })).toBe(true);
    expect(canChargeLive({ paypal_live: true, vault_seeded: false })).toBe(false);
    expect(canChargeLive({ paypal_live: false, vault_seeded: true })).toBe(false);
    expect(canChargeLive(null)).toBe(false);
    expect(paypalChip({ paypal_live: false }).tip).toContain(SIMULATE_DECLINE_LABEL);
    expect(paypalChip({ paypal_live: false }).tip).not.toContain("still work");
  });
});

describe("phone inbox translations", () => {
  it("uses the forecast's own English for the T-3 nudge it sent", () => {
    const nudge = "नमस्ते Ravi, याद दिला दें: राउंड 3 के $200.00 2027-01-01 को देय हैं।";
    const board = {
      forecasts: [
        { round_n: 3, member: "ravi", score: 0.85, flagged: true, reason: null, nudge, nudge_en: "Hi Ravi, a friendly reminder", lang: "hi", mode: "rules" },
      ],
    };
    const messages = [
      { id: 7, member: "ravi", text: nudge },
      { id: 8, member: "asha", text: nudge },
      { id: 9, member: "ravi", text: "something else" },
    ];
    expect(forecastTranslations(board, messages)).toEqual({ 7: "Hi Ravi, a friendly reminder" });
    expect(forecastTranslations(null, messages)).toEqual({});
  });
});
