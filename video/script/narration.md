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

## Script

| Time | Line | Scene | Voiceover |
|---|---|---|---|
| **Hook and problem** |  |  |  |
| 0:00.7 | L01 | s01 title | In a savings circle, someone has to be the bank, the debt collector, and the insurer. |
| 0:06.4 | L02 | s02 slide | Priya, a composite of the organizers we studied, does that for a six-member temple chit circle. By hand. |
| 0:13.6 | L03 | s02 slide | Nineteen million US households are underbanked. In Sub-Saharan Africa, over a quarter of adults saved through a savings club. |
| 0:22.9 | L04 | s03 title | Turnkeeper is her AI treasurer, on PayPal. |
| **The product, one continuous take** |  |  |  |
| 0:26.6 | L05 | s04 compile | She pastes the circle's rules, in Hindi. Turnkeeper compiles them into a frozen charter: pot order, fees, quorum, and a hash. |
| 0:35.8 | L06 | s05 join (phone) | Each member sees the fees up front, then approves the schedule once, on PayPal. |
| 0:41.5 | L07 | s06 forecast | Three days out, the forecast flags Ravi. He paid late twice, and his last payday was three weeks before the due date. He gets a nudge in Hindi. |
| 0:51.5 | L08 | s07 round 3 | Round three runs on its own. Five charges complete, each confirmed through the PayPal Agent Toolkit. Ravi's is declined, and it's labelled sandbox-injected. |
| 1:03.2 | L09 | s08 reply | Ravi replies. |
| 1:04.7 | H01 | s08 reply | शुक्रवार को सैलरी आएगी, तब भर दूंगा। *(subtitle: "Salary comes on Friday, I'll pay then.")* |
| 1:09.4 | L10 | s08 reply | Salary comes Friday. That becomes a promise with a date. |
| 1:13.6 | L11 | s09 remedies | The agent proposes only what the charter allows. The buffer covers Ravi now, he repays by PayPal invoice, and he swaps turns with Tomas, so his pot comes last. |
| 1:25.8 | L12 | s10 consent, vote, approve | Both consent. Four of six vote yes. Priya taps once. |
| 1:30.4 | L13 | s11 execute | The agent reschedules the board, and sends Ravi an invoice with a QR code. |
| 1:35.9 | L14 | s12 payout | The ledger checks out, and PayPal Payouts sends Dev the pot, net of fees, with real IDs on the receipt. |
| **Clean round** |  |  |  |
| 1:43.6 | L15 | s13 round 4 | Round four is clean. Six charges and a payout, and Priya typed nothing. |
| **How it's built** |  |  |  |
| 1:49.3 | L16 | s14 architecture | Under the hood, the model proposes, a policy engine decides, and humans approve where it matters. |
| 1:56.2 | L17 | s15 APIs | On PayPal: Vault for consent, Orders for charges, Payouts for the pot, Invoicing through the Agent Toolkit, plus Webhooks and Balances. |
| 2:06.3 | L18 | s16 AI layer | The agent's tools sit behind an MCP server. It runs on a free model, with a replay cache, and a labelled rules fallback. |
| **Evidence** |  |  |  |
| 2:15.4 | L19 | s17 evidence | Across a hundred rules-mode eval runs, ninety-five percent of proposals were charter-legal before the policy check, and zero illegal actions executed. |
| 2:26.0 | L20 | s17 evidence | And the full story ran end to end on the real PayPal sandbox. |
| **Prior art and honesty** |  |  |  |
| 2:30.8 | L21 | s18 slide | PayPal's money pooling collects for one group purchase. Turnkeeper is the pool that rotates. |
| 2:36.7 | L22 | s18 slide | It's sandbox only. Fees are shown before anyone consents, the buffer is optional, and we make no credit claims. |
| **Close** |  |  |  |
| 2:45.0 | L23 | s19 outro | Turnkeeper. Your circle, kept. |

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

Summary: 26 claims, all backed by a file. Three are worded with care (2, 15, 16). None is unbacked.
Figures that must never appear: everything in `docs/sources.md` "Do not use".
