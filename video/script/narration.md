# Turnkeeper demo video: narration

Total runtime **2:49.2** (169.2 s, cap 2:52). 23 English lines (375 words) plus one Hindi line.
Timings come from the generated audio: `video/public/audio/vo/<id>.wav` and `durations.json`. After any re-take of
a line, run `python video/script/retime.py` and it rewrites every scene's start and duration in `storyboard.json`.

- English voice: Kokoro **af_heart**, speed 0.92 (L01 and L23 at 0.88). Measured: 375 words in 146.2 s of speech,
  about 154 wpm inside a line. Across the whole video, with the pauses, that works out to about 133 wpm.
- Hindi line H01: edge-tts **hi-IN-SwaraNeural**, as the brief asked. It reads Ravi's text message aloud. It is a
  synthetic female voice, not Ravi's voice. If a male voice fits better, `hi-IN-MadhurNeural` is the edge-tts
  alternative; it is not used yet.
- No music.

## Voice choice: af_heart over bf_emma

Both voices read the same two lines (L08 and L16 drafts, in the scratchpad `voicetest/`). I compared them with a
numpy pitch and pause analysis:

| Line | Voice | Length | Pitch variation (sd) | 5-95% pitch range | Pauses > 150 ms |
|---|---|---|---|---|---|
| Round-three line | af_heart | 10.73 s (134 wpm) | 2.73 st | 8.7 st | 4 |
| Round-three line | bf_emma | 9.77 s (147 wpm) | 2.17 st | 6.7 st | 2 |
| Architecture line | af_heart | 6.76 s | 3.46 st | 10.2 st | 3 |
| Architecture line | bf_emma | 6.42 s | 2.16 st | 6.8 st | 2 |

af_heart has about 30-50% more pitch movement, so it is less flat. It also pauses at commas and full stops where a
person would, and its natural pace lands in the calm 130-140 wpm band without slowing it down. bf_emma is faster
and flatter, and Kokoro's own voice notes grade it lower (af_heart A, bf_emma B-). These numbers stand in for
listening. Have someone listen once before the final render.

## Script (v3, 2026-11-04)

Total runtime **2:56.9** (176.9 s, cap 3:00). Generated from `voiceover.json` and `storyboard.json`; English lines are Kokoro `af_heart` at speed 1.0, the Hindi line is edge-tts `hi-IN-MadhurNeural`.

| Time | Scene | Line | Text |
|---|---|---|---|
| 0:00.1 | s00-cold-open | C01 | Ravi's payment just failed. Priya, the organizer, didn't chase him. Here's what her treasurer did next. |
| 0:06.6 | s02-priya-stats | P01 | Priya, a composite organizer, is her circle's bank, collector, and insurer. |
| 0:11.4 | s02-priya-stats | P02 | Nineteen million US households are underbanked. In Sub-Saharan Africa, over a quarter of adults save in clubs. |
| 0:19.3 | s04-compile | L05 | Her rules, in Hindi, go in. Turnkeeper compiles them into a frozen charter: pot order, fees, quorum, hash. |
| 0:26.9 | s05-join-phone | L06 | Each member sees the fees up front, then approves the schedule once, on PayPal. |
| 0:32.2 | s05b-mandates | M01 | Each member also signs a mandate for their own agent: AP2-style, and server-attested. |
| 0:38.6 | s06-forecast | L07 | Three days out, the forecast flags Ravi: late twice, and paid three weeks early. He gets a nudge in Hindi. |
| 0:45.7 | s07-round3 | L08 | Round three runs itself. Five charges clear, confirmed by the PayPal Agent Toolkit. Ravi's is declined, sandbox-injected. |
| 0:55.2 | s08-ravi-reply | L09 | Ravi replies. |
| 0:56.5 | s08-ravi-reply | H01 | शुक्रवार को सैलरी आएगी, तब भर दूंगा। |
| 1:00.7 | s08-ravi-reply | L10 | Salary comes Friday. That becomes a promise with a date. |
| 1:04.6 | s09-remedies | L11 | Replayed from a real Gemini run, the agent proposes only what the charter allows: the buffer now, an invoice later, and a turn swap with Tomas. |
| 1:14.7 | s09b-injection | I01 | We planted an order in Ravi's reply: invoice five thousand. The model ignored it, and the policy would refuse it. |
| 1:22.5 | s10-agents | L12 | The members' agents answer inside their mandates. Ravi's asks him first. Four yeses are needed, and Priya taps once. |
| 1:30.5 | s10b-own-assistant | X01 | Ravi's own AI assistant can answer over MCP: one token, one question, fifteen minutes. Used for Tomas, it's refused. |
| 1:40.1 | s11-execute | L13 | The agent reschedules the board, and sends Ravi an invoice with a QR code. |
| 1:45.2 | s11b-passbooks | B01 | Every member sees the same truth on their phone: their payments, their turn, and a receipt for each charge. |
| 1:52.1 | s12-payout | L14 | The ledger checks out, and PayPal Payouts sends Dev the pot, net of fees, with PayPal's own IDs. |
| 2:01.8 | s13a-refusal | R01 | A two hundred thirty dollar charge, over Ravi's mandate, is refused before PayPal is called. |
| 2:08.4 | s13-clean-round | L15 | Round four is clean: six charges, one payout, and Priya typed nothing. |
| 2:13.4 | s14-build | A1 | The model proposes, a policy engine decides, and people and member agents act inside signed mandates. |
| 2:19.9 | s14-build | A2 | Vault, Orders, Payouts, Invoicing, Webhooks and Balances, with tools behind MCP, on a free Gemini model. |
| 2:28.2 | s17-evidence | L19 | On held-out cases, Gemini picked the preferred remedy sixteen times out of sixteen. No illegal action executed. |
| 2:35.7 | s17-evidence | L20 | And the full story ran live on the PayPal sandbox. |
| 2:39.3 | s18-prior-art-honesty | L21 | PayPal's money pooling collects for one group purchase. Turnkeeper is the pool that rotates. |
| 2:44.9 | s18-prior-art-honesty | L22 | It's sandbox only, the buffer is optional, there are no credit claims, and our mandates are AP2-style, not AP2. |
| 2:53.0 | s19-outro | L23 | Turnkeeper. Your circle, kept. |

### Planned vs actual section times

| Section | Planned | Actual | Note |
|---|---|---|---|
| Hook, problem and title | 0:00-0:20 | 0:00-0:26.3 | Includes the 3.7 s title card (L04) |
| Product take | 0:20-1:35 | 0:26.3-1:43.4 | The Hindi line and the decline beat need the room |
| Clean round | 1:35-1:50 | 1:43.4-1:49.1 |  |
| How it's built | 1:50-2:15 | 1:49.1-2:15.1 | On plan |
| Evidence | 2:15-2:30 | 2:15.1-2:30.5 | On plan |
| Prior art and honesty | 2:30-2:45 | 2:30.5-2:44.6 | On plan |
| Close | 2:45-2:50 | 2:44.6-2:49.2 | On plan |

### What the captions and screen must show (not spoken)

- s02: the source line under each figure (FDIC 2023; World Bank Global Findex 2025, 2024 data). The lower third
  says Priya is a composite persona.
- s04: the compile chip reads "rules mode: parser". The capture runs with `TK_LLM_PROVIDER=none`, so the charter
  comes from the deterministic parser. The narration never says a model compiled it.
- s07, s09, s11, s12, s13: a "sped up N×" badge whenever the footage is time-compressed. The live round 3 takes
  about 60 s.
- s13: footnote "The Judge Mode button stands in for the calendar; the organizer does nothing."
- Payout amount and IDs: read them from the captured footage. Never type them into the composition.

## Claims checklist

Each spoken or on-screen claim, with the file that backs it. ✔ = checked against code or docs on 2026-10-26.
⚠ = true, but worded with care for the reason given.

| # | Claim (line) | Backed by | Status |
|---|---|---|---|
| 1 | Someone is the bank, debt collector and insurer (L01) | `README.md` "30-second summary" | ✔ framing |
| 2 | Priya is a composite of organizers we studied (L02) | `PLAN.md` §1 Persona; `docs/sources.md` background evidence (NYC DCA 2013, Anderson et al. 2009) | ⚠ The interview-baseline table in `docs/sources.md` is still empty, so "studied" means published research, not interviews. Never say "interviewed". |
| 3 | Six-member temple chit circle (L02) | `app/fixtures/demo_circle.json`, `app/fixtures/demo_rules_hi.txt` (6 members, Temple Circle A) | ✔ |
| 4 | 19 million US households underbanked (L03) | `docs/sources.md` row 1 (FDIC 2023, 14.2% ≈ 19.0M) | ✔ |
| 5 | Over a quarter of adults in Sub-Saharan Africa saved through a savings club (L03) | `docs/sources.md` row 2 (28.1%, 2024 data, Findex 2025) | ✔ year shown in the caption |
| 6 | Rules pasted in Hindi compile to a frozen charter with pot order, fees, quorum and a hash (L05) | `web/src/pages/CompilePage.tsx`; `app/routes/compile.py`; `tests/routes/test_compile.py::test_compile_demo_text_matches_the_session_circle` | ✔ the parser in rules mode, labelled |
| 7 | Members see fees up front and approve the schedule once on PayPal (L06) | `web/src/pages/JoinPage.tsx` (fee line, "What PayPal will show you"); `app/paypal/vault.py`; `docs/d1-smoke.md` (approval page shows the plan: PASS) | ✔ |
| 8 | Forecast at T-3 flags Ravi: late twice, last payday three weeks before the due date, Hindi nudge (L07) | `app/agent/forecast.py`; `docs/live-run/raw-2026-10-25/02_advance_to_t3.json` (score 0.85, "paid late in rounds 1 and 2; last payday … 21 days before the 2027-01-01 due date", Hindi nudge) | ✔ |
| 9 | Five charges complete, each confirmed via the Agent Toolkit (L08) | `app/engine/rounds.py`; `app/paypal/toolkit_adapter.py` (`get_order_details`); `docs/live-run/2026-10-25-rerun.md` step 3 ("Confirmed via toolkit") | ✔ |
| 10 | Ravi's decline is labelled sandbox-injected (L08) | `app/engine/rounds.py` `MOCK_DECLINE_LABEL = "API decline: sandbox-injected"`; `app/paypal/orders.py` (PayPal-Mock-Response) | ✔ |
| 11 | Ravi's Hindi reply "Salary comes on Friday" becomes a dated commitment (L09-L10) | `app/engine/inbox.py` `DEFAULT_RAVI_REPLY`; rerun doc step 4 (payday 2027-01-01, a Friday) | ✔ |
| 12 | Only charter-legal remedies: buffer advance, repay by invoice, turn swap with Tomas so Ravi's pot comes last (L11) | `app/engine/recovery.py`; `app/domain/policy.py`; rerun doc steps 5 and 9 (rotation becomes meena, asha, dev, lin, tomas, ravi) | ✔ |
| 13 | Both consent; 4 of 6 vote yes; Priya taps once (L12) | rerun doc steps 6-8 ("Votes: 4 of 6 yes (quorum 4)", "Priya approved (one tap)") | ✔ |
| 14 | The agent reschedules the board and sends an invoice with a QR code (L13) | rerun doc step 9 (invoice SENT, `qr: true`); `web/src/board/`; `app/paypal/toolkit_adapter.py` | ✔ |
| 15 | Ledger check, then Payouts sends Dev the pot net of fees, real IDs on the receipt (L14) | rerun doc step 10 (pot $1,154.93 to Dev Mehta, batch `T2H98HSPL97JQ`); `app/paypal/payouts.py`; `web/src/components/PayoutReceipt.tsx` | ✔ The receipt first reads "pending"; never say "instantly" or "SUCCESS" on screen unless the footage shows it |
| 16 | Clean round 4: six charges and a payout; Priya typed nothing (L15) | rerun doc step 11 ("6 of 6 paid"); `web/src/components/Counters.tsx`; `raw-2026-10-25/10_payout.json` (`organizer_messages_typed: 0`) | ✔ with the s13 footnote: a Judge button advances the clock |
| 17 | The model proposes, a policy engine decides, humans approve where it matters (L16) | `README.md`; `app/domain/policy.py`; `docs/architecture.md` | ✔ |
| 18 | Vault, Orders, Payouts, Invoicing via the Agent Toolkit, Webhooks, Balances (L17) | `README.md` "PayPal APIs"; `docs/api-table.md` | ✔ The slide says real webhook delivery is not tested yet |
| 19 | Tools behind an MCP server; free model, replay cache, labelled rules fallback (L18) | `app/mcp/internal.py` (18 tools); `app/mcp/public.py` (4 tools); `app/agent/llm.py`; `README.md` "AI layer" | ✔ |
| 20 | 100 rules-mode runs: 95% charter-legal before policy, 0 illegal executed (L19) | `eval/RESULTS.md` §1 | ✔ said as "rules-mode". The live-model numbers (37/37 legal, 3/37 agreement) appear only on the slide. |
| 21 | Full story ran end to end on the real PayPal sandbox (L20) | `docs/live-run/2026-10-25-rerun.md` run 1, 11/11 PASS | ✔ PayPal live; AI was rules mode, and the slide says so. Run 2 failed at the payout (sandbox balance); that is not claimed. |
| 22 | PayPal money pooling collects for a group purchase; Turnkeeper rotates (L21) | `docs/sources.md` row 7; `docs/prior-art.md` | ✔ |
| 23 | Sandbox only; fees shown before consent; buffer optional; no credit claims (L22) | `README.md` "Limitations and custody", "Fees"; `docs/limitations.md` | ✔ |
| 24 | Slide: fees 3.49% + $0.49 (≈ $7.47 per $200), $0.25 per payout | `docs/sources.md` row 8; rerun doc ledger checks | ✔ |
| 25 | Slide: 684 pytest + 90 vitest tests | `pytest --collect-only` = 684 (2026-10-26); 90 `it(`/`test(` calls in `web/src` | ✔ |
| 26 | Slide: forecaster backtest precision and recall 0.647 | `eval/RESULTS.md` §3 | ✔ synthetic data, labelled |
| 27 | Gemini, replayed from a real run, proposes the remedies (L11, s09 chip) | `app/fixtures/llm_cache.jsonl` (68 turns, gemini-3.5-flash-lite); `docs/live-run/2026-11-03-takes.md`; v3 capture log shows "BY THE AI TREASURER" and "AI REPLAY" | ✔ replay, labelled on screen |
| 28 | Planted "invoice 5000" ignored by the model; the policy would refuse it (I01) | `eval/scenarios/s06-injection-amount-hi.yaml`; takes log row (e), 3 of 3 runs invoice 200.00; on-screen verdict computed by `injection_check` (`validate_remedy_set`) | ✔ |
| 29 | Ravi's own assistant answers over MCP; the token is refused for Tomas (X01) | `video/remotion/src/data/mcp_transcript.json` (real calls to `/mcp`, tokens redacted); `tests/routes/test_capabilities.py` | ✔ recorded transcript, labelled |
| 30 | Every member sees payments, turn and a receipt per charge; nobody else named (B01) | `app/engine/passbook.py`; `tests/routes/test_passbook.py` dignity test; v3 phone captures | ✔ round 1-2 rows marked replay on screen |
| 31 | Held-out Gemini eval: preferred remedy 16/16, 0 illegal executed, commitment 15/16 (L19, s17 slide) | `eval/RESULTS.md` §2 held-out table | ✔ small n, said on the slide |

Summary: 31 claims, all backed by a file (27-31 added for v3; claim 25's test counts are now 889 pytest + 131 vitest). Three are worded with care (2, 15, 16). None is unbacked.
Figures that must never appear: everything in `docs/sources.md` "Do not use".
