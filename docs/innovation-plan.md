# Innovation plan (judge panel verdict, 2026-10-27)

**Turnkeeper judge-panel verdict (2026-10-27)**

I read the README, architecture and prior-art docs, `app/domain/mandate.py`, the mandate and `flags_json` hooks in `policy.py` and `models.py`, and the research.md headings. I did not re-read the N1, N2b and N2d bodies. The three reports are taken as given. The score and odds figures below are panel judgment, not measurements.

## 1. Why Innovation is about 6.5/10 today

1. **The headline idea is familiar.** "AI treasurer for savings circles" maps straight onto things we have seen: MoneyFellows, SusuFlow (it even calls itself an "AI Treasurer"), LedgerLoop and Tanda. Our own N2d scan found no ROSCA among the 117 entries, but that does not make the idea new to a judge. We read it as a better-engineered version of a known idea.
2. **What is new is a workflow, not a mechanism.** Predict, decline, charter-legal remedy, vote, reschedule and ledger-checked payout is well built. The README's Innovation row lists it, but nothing in it is a new mechanism or a new kind of agent behaviour. "LLM proposes, policy engine decides, human approves" and "agent has a spend cap" are table stakes by 2026.
3. **The agency in the demo is partly faked.** Steps 6 and 7 are "Consent as the affected members" and "Vote as members", which a judge clicks. The multi-party part, which is the interesting part, is simulated and labelled as such. Honest, but it reads as plumbing rather than novelty.
4. **Authority runs one way.** The organizer holds the only mandate. Members consent once, and after that the treasurer is bounded only by operator-side rules. Nothing in the product lets a judge say "I haven't seen that."
5. **Our strongest differentiator is invisible.** The frozen, hashed charter that binds consent, remedies and payout is real. It appears only as a hash in a table and is not a demo moment.
6. **Breadth earns Tech, not Innovation.** Seven PayPal surfaces and 711 tests help Technological implementation and Presentation. They do not move Innovation.

Target after the build: **Innovation 8.0**, by giving the circle a new authority model that is visible on screen.

## 2. Ranking by expected gain per day

Gain is the summed expected score change across all five criteria, discounted by the chance of finishing, divided by days of work. "Demo" is how well it plays in the 3-minute video.

| Rank | Candidate | Days | Gain (sum of 5 criteria) | Demo | Risk | Gain per day |
|---|---|---|---|---|---|---|
| **1** | **Member Agents with member mandates** (reports B+C: member mandates plus member agents voting under them) | 3.5–4 | about +2.4 (Innovation +1.2, Tech +0.4, Design +0.3, Presentation +0.4, Impact +0.1) | **Excellent.** A visible refusal plus agent votes, one escalation to a human | Low–medium behind a flag | **about 0.6** |
| **2** | **Public MCP, narrow scope** (report C item 5 minus `contribute`: `get_member_mandate`, `get_join_link`, `answer_request`) | 1.5–2 | about +1.0 (Innovation +0.4, Tech +0.3, Presentation +0.2, Agentic Commerce and PayPal+AI special-prize fit) | Good, if a real recorded external-client transcript is shown | Medium (public surface) | about 0.55, and it depends on #1 |
| 3 | Exposure-optimal or risk-ordered rotation with a Bryntum exposure curve | 1.5 | about +0.5 | Good, drag and curve | Low | about 0.33 (Bryntum odds are already 25–30%) |
| 4 | Sponsor or nonprofit mode | 1 | about +0.3 (Impact only) | Weak | Low | about 0.3. Write it up in Devpost as roadmap, do not build it |
| 5 | Auction chit mode | 5–7 | about +1.8 if finished | Fine | **High** | about 0.15 expected |
| 6 | Chat-native interface | 2–2.5 | about +0.5 | Fine | Low | about 0.22 |
| 7 | FX display | 1 | about +0.2 | Weak | Very low | about 0.2 |
| 8 | Per-member priced cover or premiums | 4+ | about +1.4 on paper | Fine | Very high | about 0.1 |
| 9 | Venmo payout | 0.5 (one test) | about +0.1 | Only if it works | Medium | Skip |
| 10 | PayPal remote MCP | n/a | n/a | Needs OAuth login | n/a | Skip |

**Why the auction loses, although Report A wanted it.** Report C's code facts decide it. A new charter field changes every charter hash. The auction also breaks the fixed `rotation`, the payout `recipients` order and `expected_recipient`, along with the collateral rule and swap handling. The dividend has no home, because `policy._charge` forbids variable amounts, and everywhere assumes one flat `charter.amount`. That is 5–7 days of invariant surgery for a 60-second beat.

**Why premiums lose.** They break the one-price Vault plan and drift toward the credit-scoring claims `prior-art.md` deliberately avoids.

**Why the others lose.** Candidates 3 and 6 to 10 are polish or low-payoff.

## 3. Recommendation

### Primary: Member Agents (delegated authority chain)

The chain is member, then member's agent, then treasurer, then PayPal. Each link is checked by deterministic code.

**Naming and pitch.** Do not headline "mandates". Report C is right that it is a crowded keyword. Pitch it as **"agents governing a shared pool, bounded by each member."**

**What it is.**
- Each member holds a `MemberMandate`: max charge per round, expiry, charter hash, and swap and advance rules.
- Each member's agent answers remedy consents and votes inside that mandate. Its answer is yes, no, or ask-the-human.
- The decision is deterministic. The LLM only words the explanation, through `llm.py` with replay and rules fallback.
- The charge gate in `policy._charge` refuses any charge outside a required mandate before PayPal is called.
- It replaces the click-to-fake consents in steps 6 and 7 with real decisions, which also helps honesty.

**Honest labels (required).**
- "AP2-style Intent Mandate, our own implementation. PayPal's AP2 APIs are not public, and we do not claim AP2 support."
- "Server-attested HMAC signature, not a key held by the member."

### Secondary (one, trimmed): Member-side public MCP

Add three tools to the demo-only public MCP:
- `get_member_mandate`.
- `get_join_link`. A human still approves on PayPal, so there is no money tool in chat.
- `answer_request`. It calls the same `decide()` as the primary feature.

Do not ship `contribute`. Scope it to demo circles and `judge_mini`, with the existing bearer token and 30 requests/min cap.

This is the same story as the primary, told from the outside: "your own assistant can be your member agent, bounded by the same mandate." It earns Best Use of Agentic Commerce and Best Use of PayPal+AI eligibility.

### Day-by-day plan

Dates are Oct 28 to Nov 4, with feature freeze at **Nov 4, 18:00**. Nov 5–11 is for the video, Devpost, interviews and buffer. Submit by Nov 11, one day before the deadline.

| Day | Work | Exit gate |
|---|---|---|
| **Oct 28** | Baseline first: run the full suite and diff against `replay_canonical.json`. Add a charter-hash stability snapshot test. Add the `member_mandates` flag in `flags_json` (default off) and the env kill switch. Write pure `domain/member_mandate.py` (model, sign and verify reusing `canonical_dumps` and the HMAC helper, expiry, hash mismatch, tamper). About 20 tests. | Baseline green; the new module is pure and unit-tested |
| **Oct 29** | `MemberMandateRow` (new table only, no column changes). Optional `member_mandate_ok: dict \| None` on `CircleState`; `None` means not required, so all existing tests stay green. `_charge` denies when it is required and missing or invalid. `state.circle_state` fills it. Fixture-clone seeding. `GET /api/circles/{id}/mandates`. **Flag-off regression: the story is identical to baseline.** | 711 existing tests plus the new ones green; flag-off regression passes |
| **Oct 30** | `engine/member_agent.py` with a decision table: charter-legal and shortfall-covered auto-yes; swaps that move the member earlier are accepted and later ones ask; advance over cap is no; not-covered is no. It records consents and votes with `source="agent"`. "Ask" posts to the inbox and leaves the vote pending. LLM wording with replay and rules fallback. `POST .../member-agents/run`. About 20 tests. | Table-driven tests green; works with no AI key |
| **Oct 31** | UI. A Mandates card (limit, expiry, rules, "signature verified" chip). Vote and consent rows read "Tomas's agent · rule: accepts earlier turn". An Advanced Judge action, "Try a $230 charge," that calls `policy.authorize` only and never reaches PayPal. Vitest. **Decision gate: if anything is behind schedule, drop the secondary feature entirely.** | The refusal moment renders; vitest green |
| **Nov 1** | Integration as a separate "Story B" run, opt-in per run, never the default. Run it once live on the sandbox end to end. Record `replay_member_agents.json` with `scripts/record_replay.py`. Deploy with the flag off. | One live Story B run and its recording; production flag-off story still passes |
| **Nov 2** | Secondary: the three MCP tools, scoped to demo circles. About 15 tests (PII masking, rate limit, scope denial). Copyable MCP config snippet on the Judge page. | Tools pass tests; ship `answer_request` before anything else; `get_join_link` last |
| **Nov 3** | Record one real external-client transcript (a scripted MCP client such as Claude Code, labelled "recorded transcript"). Docs: README criteria table, architecture, limitations wording. Slip buffer. | Transcript saved; docs updated; research.md and changelog updated per the memory rule |
| **Nov 4** | Hardening only. Full suite. Flag-flip rehearsal against `replay_canonical.json`. Decide whether Story B appears in the Judge bar. **Freeze 18:00 and tag the release.** | Green suite; tag `feature-freeze` cut |

**Single permitted stretch (decide only at the end of Oct 31):** browser-side Ed25519 signing, 0.5–1 day, which would make "member-signed" literally true. If Oct 31 is not clean, skip it and keep the "server-attested" label.

**If the schedule slips:**
- Behind at Oct 31: drop the secondary feature.
- Behind at Nov 2: ship only `get_join_link` and read.
- Behind at Nov 4: ship the primary behind the flag and record from the replay.

### The exact on-screen demo moment (about 45 seconds)

1. Round 3 runs on the live sandbox: five captures confirmed by the toolkit, and Ravi's declined with "sandbox-injected."
2. The recovery set opens. Tomas's card shows "limit $200 per round · accepts earlier turn · never a retry fee · signature verified · charter 3f9a…".
3. **Tomas's agent votes YES in about 2 seconds**, with the trace "rule: swap moves you earlier, shortfall covered."
4. **Asha's agent answers ASK.** The swap would move her payout two rounds later. Her phone inbox pings and she taps yes. This is what shows the agent is not a rubber stamp.
5. Priya taps once. The board redraws the swap.
6. **The hold shot, 3 full seconds:** "Treasurer attempts to charge Ravi $230." A red banner reads **"DENIED before PayPal is called: exceeds Ravi's mandate ($200)."**
7. Brief cut to the recorded external-assistant transcript answering a swap request, labelled as recorded.

### Two sentences for the video script

"Every member holds a signed mandate, and their own agent votes on swaps inside it, escalating to the human when a request is out of scope. Turnkeeper cannot charge one cent past any member's mandate, so watch it refuse a $230 charge before PayPal is ever called."

### Devpost, two sentences

"Turnkeeper's treasurer is bounded by each member's own mandate (an AP2-style Intent Mandate, our own implementation, since PayPal's AP2 APIs are not public). Member agents vote on remedies within that delegated authority and escalate to the human when a proposal falls outside it; in this sandbox build the mandates are server-attested HMAC signatures, not member-held keys."

Add one line for the secondary feature: "A recorded external-assistant transcript shows the same member decision flow through the public MCP; money only moves after a human approves on PayPal."

### What must NOT be built

- The auction, dividends, or any variable per-member amount.
- Premiums, risk-priced fees, or any credit-scoring language.
- Any new charter field. It changes every hash, so use only the external `member_mandate` table and policy input.
- `contribute` or any other money-moving tool on the public MCP.
- PayPal's remote MCP, Agent Ready or Store Sync.
- Venmo payouts and FX payouts.
- A chat or WhatsApp interface.
- A sponsor or CDFI dashboard (Devpost roadmap text only).
- Any LLM that makes the decision. It only words the explanation.
- Any claim of PayPal AP2 support, "member-held keys" (unless the Ed25519 stretch ships) or "insurance."
- Any change to the default Judge story path before the Nov 4 gate.

### Rollback plan

1. **Circle flag.** `member_mandates` in `flags_json` is default off. It is enabled only for Story B runs and Advanced Judge. With the flag off, `member_mandate_ok` is `None`, so the policy path is unchanged. A regression test proves the flag-off story matches the baseline.
2. **Global kill switch.** An environment variable (`TK_MEMBER_AGENTS=0`) makes the new routes return 404 and hides the MCP tools. Flipping it needs no deploy of old code.
3. **Additive schema only.** One new table and no changes to existing columns, so rollback never touches data.
4. **Tagged stable build.** Tag the current build `stable-oct27` before any merge, and redeploy that tag if anything misbehaves on judging days.
5. **Video fallback.** If Story B misbehaves live on recording day, use the recorded `replay_member_agents.json`, labelled `simulated` and served through the existing `/api/replay` path.

## 4. Expected effect on the odds of a main prize

Innovation 6.5 to about 7.5–8.0 is a gain of +1 to +1.5 on one of five equal criteria. Tech, Design and Presentation each add a small gain, together worth about +1 point of total. That is roughly 2.5–4% of the total score.

| Outcome | Before | After |
|---|---|---|
| Top 3 overall | about 8–12% | about 11–16% |
| Any main prize or honorable mention | about 25–35% | about 30–40% |
| Best Use of Agentic Commerce or PayPal+AI special prize | not scored separately | modest relative lift; the member-side MCP and "approval before anything is paid" fit PayPal's Anthropic-partnership framing |
| Any prize at all | about 40–50% | about 45–55% |

The gain is modest, and there are two caveats.
- **The gain is not guaranteed.** About 15% of the time the feature lands half-finished. The flags contain the damage to lost days, not a broken demo.
- **The video is still the biggest lever.** A sharp, honest video is worth about 1.5×, which is more than this feature. That is why the freeze is Nov 4 and not later: it leaves Nov 5–11 to produce the video and the interviews, plus slip buffer.

**Decision:** build Member Agents first, add the trimmed MCP only if Oct 31 is clean, and freeze Nov 4.

Files referenced:
- app/domain/mandate.py
- app/domain/policy.py
- app/models.py
- README.md
- docs/prior-art.md
- research notes (kept outside the repo)
