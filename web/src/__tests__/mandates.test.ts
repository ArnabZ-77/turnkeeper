import { describe, expect, it } from "vitest";
import { demoBoard } from "./fixtures";
import { initialState, reducer } from "../state";
import {
  MANDATE_LABEL,
  NO_DECISIONS,
  SIGNATURE_LABEL,
  agentRows,
  answerBody,
  decisionsFor,
  mandateRows,
  mandatesOn,
  memberAgentsRequested,
  mergeDecisions,
  overchargeView,
  parseDecisions,
  parseMandates,
  parseOvercharge,
  pendingAsks,
  ruleLabel,
  signatureChip,
  storyVariant,
  verifiedSummary,
} from "../mandates";
import type { RecoveryInfo } from "../types";

const mandate = (over: Record<string, unknown> = {}) => ({
  member: "tomas",
  name: "Tomas Novak",
  present: true,
  signature_verified: true,
  status: "ok",
  max_charge_per_round: "200.00",
  max_advance: "200.00",
  expires: "2027-06-01",
  approved_at: "2026-11-01T00:00:00Z",
  charter_sha256: "abc",
  rules: { accept_earlier_swap: true, max_advance: "200.00", require_ask_for_later_swap: true, summary: "x" },
  signature: "0123456789abcdef…",
  ...over,
});

const view = (members: unknown[] = [mandate()], over: Record<string, unknown> = {}) => ({
  circle_id: "abc123",
  enabled: true,
  charter_sha256: "abc",
  label: MANDATE_LABEL,
  signature_label: SIGNATURE_LABEL,
  members,
  ...over,
});

const rec = (over: Partial<RecoveryInfo> = {}): RecoveryInfo => ({
  status: "voting",
  set_id: "rs_00000001",
  member: "ravi",
  round_n: 3,
  commitment: null,
  remedies: [],
  problems: [],
  votes: { yes: 1, no: 0, needed: 4, total: 6, passed: false, closes_at: null },
  consents: { needed: ["ravi", "tomas"], given: [], refused: [] },
  approval: null,
  invoice: null,
  ...over,
});

const dec = (over: Record<string, unknown> = {}) => ({
  member: "tomas",
  kind: "consent",
  choice: "yes",
  reason: "rule: swap moves you earlier, shortfall covered",
  rule: "swap_earlier",
  recorded: true,
  source: "agent",
  asked: false,
  ...over,
});

describe("flag detection (everything hidden unless on)", () => {
  it("mandatesOn needs flags.member_mandates === true", () => {
    expect(mandatesOn(null)).toBe(false);
    expect(mandatesOn(demoBoard())).toBe(false); // the default circle: the key is absent
    const on = demoBoard();
    on.circle.flags = { ...on.circle.flags, member_mandates: true };
    expect(mandatesOn(on)).toBe(true);
    on.circle.flags = { ...on.circle.flags, member_mandates: false };
    expect(mandatesOn(on)).toBe(false);
  });

  it("the URL opt-in and the story variant", () => {
    expect(memberAgentsRequested("")).toBe(false);
    expect(memberAgentsRequested("?member_agents=1")).toBe(true);
    expect(memberAgentsRequested("?member_agents=0")).toBe(false);
    expect(storyVariant(demoBoard(), undefined, "")).toBeUndefined(); // default story: no variant param at all
    expect(storyVariant(demoBoard(), "member_agents", "")).toBe("member_agents");
    expect(storyVariant(demoBoard(), undefined, "?member_agents=1")).toBe("member_agents");
    const b = demoBoard();
    b.circle.flags = { ...b.circle.flags, member_mandates: true, member_agents_story: true };
    expect(storyVariant(b, undefined, "")).toBe("member_agents");
  });
});

describe("parseMandates", () => {
  it("accepts an enabled view and keeps the server labels", () => {
    const v = parseMandates(view())!;
    expect(v.members).toHaveLength(1);
    expect(v.label).toBe(MANDATE_LABEL);
    expect(v.signature_label).toBe(SIGNATURE_LABEL);
    expect(v.members[0].rules?.accept_earlier_swap).toBe(true);
  });

  it("falls back to the honest labels when the server sends none", () => {
    const v = parseMandates(view([mandate()], { label: "", signature_label: undefined }))!;
    expect(v.label).toContain("do not claim AP2 support");
    expect(v.signature_label).toContain("not a key held by the member");
  });

  it("is null for off, malformed or non-object bodies", () => {
    expect(parseMandates(null)).toBeNull();
    expect(parseMandates("nope")).toBeNull();
    expect(parseMandates(view([], { enabled: false }))).toBeNull();
    expect(parseMandates({ enabled: true })).toBeNull();
  });

  it("drops member rows without a handle instead of throwing", () => {
    const v = parseMandates(view([mandate(), { name: "x" }, null, "str"]))!;
    expect(v.members.map((m) => m.member)).toEqual(["tomas"]);
  });
});

describe("signature chip and rows", () => {
  it("verified, missing and invalid mandates", () => {
    expect(signatureChip({ present: true, signature_verified: true, status: "ok" })).toMatchObject({
      cls: "tk-chip-good",
      label: "signature verified",
    });
    expect(signatureChip({ present: false, signature_verified: false, status: "" }).label).toBe("no mandate");
    const bad = signatureChip({ present: true, signature_verified: false, status: "the mandate expired on 2026-01-01" });
    expect(bad.cls).toBe("tk-chip-critical");
    expect(bad.label).toContain("expired");
  });

  it("formats limit, expiry and rule lines per member", () => {
    const rows = mandateRows(parseMandates(view()), "USD");
    expect(rows).toHaveLength(1);
    expect(rows[0].limit).toBe("$200.00 / round");
    expect(rows[0].expires).toContain("expires");
    expect(rows[0].rules).toBe("accepts earlier turn · asks before later turn");
    expect(rows[0].meta).toBe(`advance up to $200.00 · ${rows[0].expires}`);
    expect(rows[0].signature).toBe("0123456789abcdef…");
  });

  it("a member without a mandate gets honest placeholders", () => {
    const rows = mandateRows(
      parseMandates(view([mandate({ present: false, signature_verified: false, max_charge_per_round: null, expires: null, rules: null, signature: null })])),
    );
    expect(rows[0].limit).toBe("no limit on file");
    expect(rows[0].rules).toBe("");
    expect(rows[0].chip.label).toBe("no mandate");
  });

  it("summarises how many signatures verified", () => {
    const v = parseMandates(view([mandate(), mandate({ member: "lin", signature_verified: false, status: "expired" })]));
    expect(verifiedSummary(v)).toBe("1 of 2 verified");
    expect(verifiedSummary(null)).toBe("");
  });
});

describe("parseDecisions", () => {
  it("reads decisions from a run response or a bare list", () => {
    const body = { set_id: "rs_00000001", decisions: [dec(), dec({ member: "ravi", choice: "ask", recorded: false, source: null, asked: true, rule: "swap_later_ask" })] };
    const out = parseDecisions(body);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ member: "tomas", kind: "consent", choice: "yes", source: "agent", recorded: true });
    expect(out[1]).toMatchObject({ choice: "ask", recorded: false, source: null, asked: true });
    expect(parseDecisions(body.decisions)).toHaveLength(2);
  });

  it("drops malformed entries and unknown kinds or choices", () => {
    expect(parseDecisions(null)).toEqual([]);
    expect(parseDecisions({ decisions: "x" })).toEqual([]);
    expect(parseDecisions({ decisions: [dec({ kind: "nope" }), dec({ choice: "maybe" }), { kind: "vote" }, dec()] })).toHaveLength(1);
  });

  it("never keeps a source for an unrecorded decision", () => {
    const [d] = parseDecisions([dec({ recorded: false, source: "agent", choice: "ask" })]);
    expect(d.source).toBeNull();
  });
});

describe("mergeDecisions and decisionsFor", () => {
  it("newest decision per kind+member wins; a new set starts clean", () => {
    const first = parseDecisions([dec({ member: "ravi", choice: "ask", recorded: false, source: null })]);
    let s = mergeDecisions(NO_DECISIONS, "rs_00000001", first);
    expect(s.items).toHaveLength(1);
    const answered = parseDecisions([dec({ member: "ravi", choice: "ask", recorded: true, source: "member", recorded_choice: "yes" })]);
    s = mergeDecisions(s, "rs_00000001", [...answered, ...parseDecisions([dec({ member: "meena", kind: "vote" })])]);
    expect(s.items).toHaveLength(2);
    expect(s.items.find((d) => d.member === "ravi")?.source).toBe("member");
    s = mergeDecisions(s, "rs_00000002", parseDecisions([dec()]));
    expect(s.items).toHaveLength(1);
    expect(s.setId).toBe("rs_00000002");
  });

  it("decisionsFor only returns decisions of the open set", () => {
    const s = mergeDecisions(NO_DECISIONS, "rs_00000001", parseDecisions([dec()]));
    expect(decisionsFor(s, rec())).toHaveLength(1);
    expect(decisionsFor(s, rec({ set_id: "rs_00000009" }))).toEqual([]);
    expect(decisionsFor(s, null)).toEqual([]);
  });
});

describe("agentRows", () => {
  const board = demoBoard();
  board.members = [
    { handle: "tomas", name: "Tomas Novak", lang: "en", payout_wallet: "PAYPAL", vaulted: true, rotation_pos: 1 },
    { handle: "ravi", name: "Ravi Kumar", lang: "hi", payout_wallet: "PAYPAL", vaulted: true, rotation_pos: 2 },
  ];

  it("an agent answer reads \"Tomas's agent · rule: accepts earlier turn\" with the agent badge", () => {
    const [row] = agentRows(parseDecisions([dec()]), rec(), board);
    expect(row.line).toBe("Tomas's agent · rule: accepts earlier turn");
    expect(row).toMatchObject({ state: "agent", badge: "agent", choice: "yes", status: "yes" });
  });

  it("an ask nobody answered is pending - agent asked the human", () => {
    const rows = agentRows(
      parseDecisions([dec({ member: "ravi", choice: "ask", recorded: false, source: null, asked: true, rule: "swap_later_ask" })]),
      rec(),
      board,
    );
    expect(rows[0].state).toBe("pending");
    expect(rows[0].badge).toBe("pending");
    expect(rows[0].status).toBe("pending - agent asked the human");
    expect(rows[0].detail).toContain("consent: waiting for Ravi");
    expect(rows[0].line).toBe("Ravi's agent · rule: asks before a later turn");
    expect(pendingAsks(rows)).toHaveLength(1);
  });

  it("a human's tap and the judge's click are badged as human", () => {
    const [human] = agentRows(parseDecisions([dec({ choice: "ask", source: "member", recorded_choice: "no" })]), rec(), board);
    expect(human).toMatchObject({ state: "human", badge: "human", choice: "no" });
    const [judge] = agentRows(parseDecisions([dec({ choice: "ask", source: "judge", recorded_choice: "yes" })]), rec(), board);
    expect(judge).toMatchObject({ state: "judge", badge: "human (judge click)", choice: "yes" });
  });

  it("a pending consent that the recovery block now lists as given was answered meanwhile", () => {
    const pending = parseDecisions([dec({ member: "ravi", choice: "ask", recorded: false, source: null })]);
    const [row] = agentRows(pending, rec({ consents: { needed: ["ravi"], given: ["ravi"], refused: [] } }), board);
    expect(row).toMatchObject({ state: "judge", choice: "yes" });
    expect(pendingAsks([row])).toHaveLength(0);
  });

  it("after a reload (no decisions) the server-kept sources still give the badges", () => {
    const r = rec({
      votes: {
        yes: 2,
        no: 0,
        needed: 4,
        total: 6,
        passed: false,
        closes_at: null,
        voters: [
          { member: "tomas", choice: "yes", source: "agent" },
          { member: "ravi", choice: "pending" },
        ],
      },
      consents: { needed: ["ravi", "tomas"], given: ["ravi", "tomas"], refused: [], sources: { tomas: "agent", ravi: "judge" } },
    });
    const rows = agentRows([], r, board);
    const by = Object.fromEntries(rows.map((x) => [x.key, x]));
    expect(Object.keys(by).sort()).toEqual(["consent:ravi", "consent:tomas", "vote:tomas"]);
    expect(by["vote:tomas"]).toMatchObject({ state: "agent", badge: "agent", choice: "yes", line: "Tomas's agent" });
    expect(by["consent:ravi"]).toMatchObject({ state: "judge", badge: "human (judge click)", choice: "yes" });
  });

  it("without sources (flag off) the recovery block adds no agent rows", () => {
    expect(agentRows([], rec({ consents: { needed: ["ravi"], given: ["ravi"], refused: [] } }), board)).toEqual([]);
  });

  it("a pending vote stays pending after quorum, and says so", () => {
    const pending = parseDecisions([dec({ member: "ravi", kind: "vote", choice: "ask", recorded: false, source: null })]);
    const [row] = agentRows(pending, rec({ votes: { yes: 4, no: 0, needed: 4, total: 6, passed: true, closes_at: null } }), board);
    expect(row.state).toBe("pending");
    expect(row.status).toBe("pending - agent asked the human");
    expect(row.detail).toContain("quorum already reached");
  });

  it("falls back to the handle and a readable rule for unknown names and rules", () => {
    const [row] = agentRows(parseDecisions([dec({ member: "zed", rule: "some_new_rule" })]), rec(), board);
    expect(row.line).toBe("zed's agent · rule: some new rule");
    expect(ruleLabel("swap_earlier")).toBe("accepts earlier turn");
  });

  it("answerBody names the set and the member", () => {
    expect(answerBody("rs_00000001", "ravi", "yes")).toEqual({ set_id: "rs_00000001", answers: { ravi: "yes" } });
    expect(answerBody(null, "ravi", "no")).toEqual({ answers: { ravi: "no" } });
  });
});

describe("try a $230 charge", () => {
  const denied = {
    ok: true,
    action: "try_overcharge",
    denied: true,
    reason: "ravi's mandate allows at most 200.00 per round (asked 230.00)",
    banner: "DENIED before PayPal is called: x",
    member: "ravi",
    amount: "230.00",
    round_n: 3,
    paypal_called: false,
  };

  it("shows the denial reason inline and that PayPal was not called", () => {
    const view = overchargeView(parseOvercharge(denied), "USD")!;
    expect(view.tone).toBe("critical");
    expect(view.headline).toBe("Denied: $230.00 on Ravi's mandate");
    expect(view.reason).toBe(denied.reason);
    expect(view.paypal).toBe("PayPal was not called");
  });

  it("an allowed answer is labelled as such, still without a PayPal call", () => {
    const view = overchargeView(parseOvercharge({ ...denied, denied: false, reason: "within the mandate" }))!;
    expect(view.tone).toBe("good");
    expect(view.headline).toContain("Allowed by policy");
  });

  it("falls back to the banner when the reason is missing and ignores bad bodies", () => {
    expect(overchargeView(parseOvercharge({ denied: true, banner: "DENIED before PayPal is called: x" }))!.reason).toContain("DENIED");
    expect(parseOvercharge({ ok: true })).toBeNull();
    expect(overchargeView(null)).toBeNull();
  });
});

describe("reducer", () => {
  it("starts with nothing: the default UI has no mandates, no decisions, no overcharge", () => {
    expect(initialState.mandates).toBeNull();
    expect(initialState.agentDecisions).toEqual(NO_DECISIONS);
    expect(initialState.overcharge).toBeNull();
  });

  it("mandates null on a null state is the same state (no re-render for the default circle)", () => {
    expect(reducer(initialState, { type: "mandates", mandates: null })).toBe(initialState);
  });

  it("stores mandates, merges decisions per set, keeps the overcharge answer, and resetRun clears all three", () => {
    let s = reducer(initialState, { type: "mandates", mandates: parseMandates(view()) });
    expect(s.mandates?.members).toHaveLength(1);
    s = reducer(s, { type: "agentDecisions", setId: "rs_00000001", decisions: parseDecisions([dec()]) });
    s = reducer(s, { type: "agentDecisions", setId: "rs_00000001", decisions: parseDecisions([dec({ member: "meena", kind: "vote" })]) });
    expect(s.agentDecisions.items).toHaveLength(2);
    s = reducer(s, { type: "overcharge", result: parseOvercharge({ denied: true, reason: "r" }) });
    expect(s.overcharge?.denied).toBe(true);
    const cleared = reducer(s, { type: "resetRun" });
    expect(cleared.mandates).toBeNull();
    expect(cleared.agentDecisions).toEqual(NO_DECISIONS);
    expect(cleared.overcharge).toBeNull();
  });

  it("an empty decisions list for the same set changes nothing", () => {
    const s = reducer(initialState, { type: "agentDecisions", setId: "rs_00000001", decisions: parseDecisions([dec()]) });
    expect(reducer(s, { type: "agentDecisions", setId: "rs_00000001", decisions: [] })).toBe(s);
  });
});
