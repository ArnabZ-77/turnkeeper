# Devpost writeup draft (paste into the project's "Project details")

> Every claim below is true of the code today. Facts and numbers come from README.md, docs/sources.md,
> docs/d1-smoke.md and eval/RESULTS.md. Fill the YouTube and live-demo URLs when they exist.

## Elevator pitch (<= 200 chars)

An AI treasurer for rotating savings circles: members approve once on PayPal, then it collects every round, recovers shortfalls, and pays the pot out.

## Inspiration

A savings circle (chit fund, susu, tanda, chama, stokvel) runs on one person's goodwill. Every round that person
collects the money, chases the late payers, covers shortfalls from their own pocket, and hopes nobody takes an early
pot and disappears. Hundreds of thousands of groups run this way on WhatsApp and a spreadsheet: for example more
than 11 million South Africans belong to stokvels (NASASA, 2025), and 28% of adults in Sub-Saharan Africa saved
through a savings club in 2024 (World Bank Findex 2025).

PayPal retired Money Pools in 2021 and relaunched money pooling in November 2024. A pool collects once for one
purchase. A savings circle is the pool that *rotates*, every month, with rules. That is what we built.

## What it does

Turnkeeper is an AI treasurer. The organizer pastes the circle's rules (English or Hindi). It compiles them into a
**frozen charter** (typed, SHA-256 hashed) and a PayPal billing plan. Then:

1. **Members approve once on PayPal** (Vault v3, `RECURRING_PREPAID`). PayPal's own approval page shows the plan.
2. **Every round is collected automatically** with merchant-initiated charges on the saved wallets, each capture
   confirmed with the PayPal Agent Toolkit.
3. **Shortfalls are flagged three days early** by a risk score, with a nudge in the member's language.
4. **A decline becomes the fixes the charter allows**: a buffer advance repaid by a PayPal invoice with a QR code,
   a turn swap, or a dated retry. The affected members consent, the group votes, the organizer taps once, and the
   agent **reschedules the circle** on a Bryntum board.
5. **The pot is paid out** with PayPal Payouts after the circle's own ledger and a mandate check say the money is
   there. On a clean round nobody clicks anything.

The model proposes, deterministic code decides (`app/domain/policy.py`), and humans approve where it matters.

## How we built it

- **PayPal (sandbox):** Vault v3 (setup tokens with a recurring billing plan, payment tokens), Orders v2
  (merchant-initiated `CAPTURE` on a vault id), Payouts, Invoicing and order details through the **PayPal Agent
  Toolkit**, Webhooks (signature verification, dedupe), Reporting/Balances. One FIFO gateway with a token bucket,
  a cached OAuth token and a `PayPal-Request-Id` on every call; our adapter makes the toolkit share that token and
  idempotency.
- **AI:** one provider layer (`app/agent/llm.py`) with a **free** model by default (Gemini or Groq through the
  OpenAI-compatible SDK), Anthropic as an optional switch, a **replay cache** and a **rules-mode fallback**: the whole
  demo works with no model key, and every screen labels whether a step was live, replayed or rule-based. The agent
  drives the money tools through an in-process **MCP server**; a public read-only MCP server is also exposed.
- **Backend:** Python, FastAPI, SQLite. **Frontend:** React, Vite, **Bryntum Scheduler** (the rotation board).
- **Built with** Claude Code and the APIMatic PayPal Context Plugin.

## Challenges

- Sandbox quirks found by testing every flow live: the recurring-consent billing plan needs `one_time_charges` and
  `pricing_model: VARIABLE`; PayPal Balances lags about 2.5 hours (so the circle's ledger, not Balances, gates
  payouts); cancelling an unclaimed payout returns a server error in sandbox (so we re-route as a new batch and say
  so on screen).
- Making the AI honest: the first live-model eval matched the charter's preferred remedy only 3 of 37 times. We
  traced it to a missing stated preference and made the legal options precomputed; the held-out score is now 16 of
  16 runs, with the caveat that the task is easier by design (see `eval/RESULTS.md`).
- Keeping every label true: the injected decline is labelled "API decline: sandbox-injected", and a simulated round
  is never paid out.

## Accomplishments we're proud of

- The full story runs end to end against the **real PayPal sandbox**: five charges, a decline, a vote, an invoice,
  a payout, and a hands-free round.
- 700+ automated tests with PayPal and every model mocked, plus a forecaster backtest and a 20-scenario remedy eval.
- Zero illegal actions executed in any eval run.

## What we learned

Money tools need a deterministic gate between the model and PayPal; free models are good enough for explanation and
parsing when the legal options are computed for them; and an honest "rules mode" makes a demo robust to quotas.

## What's next

Real organizer interviews, Venmo payouts, a nonprofit/CDFI multi-circle view, and gross-up fee policies. Production
use would need PayPal's Payouts approval and a custody review; we claim no credit-building outcomes.

## Built with

Python, FastAPI, SQLite, React, Vite, TypeScript, Bryntum Scheduler, PayPal Vault, Orders, Payouts, Invoicing,
Webhooks, PayPal Agent Toolkit, MCP (FastMCP), Gemini / Groq (OpenAI-compatible), Remotion, Playwright, Kokoro TTS.

## Judge access

- Try it: `<hosted URL>` (Judge Mode story button; no login needed).
- Code: https://github.com/ArnabZ-77/turnkeeper (MIT).
- Video: `<YouTube URL>`.
- Run it locally with the README quickstart (replay mode needs no keys).
- Narration in the video is AI text-to-speech.

## Sponsor notes

- **Bryntum:** the Scheduler is the main surface; the agent's turn swap and the invoice-due event are drawn on it.
- **APIMatic:** the PayPal Context Plugin was used while building the PayPal integration.
