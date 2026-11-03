# Limitations and custody

What Turnkeeper is not, what it does not yet do, and where the money sits. Kept current with the code; if this
page and the code disagree, the code wins and this page is a bug.

## Scope

- **Sandbox only.** `app/config.py` refuses to start unless `PAYPAL_ENV=sandbox`, and the only base URL is
  `https://api-m.sandbox.paypal.com`. No real money moves. A production version would need PayPal's approval for
  Payouts and a review of this use case.
- **No credit claims.** Turnkeeper does not report to credit bureaus and does not claim to build credit. Lending
  circle outcomes in [prior-art.md](prior-art.md) describe formal programmes, not this app.
- **Not a lender, not KYC.** No identity checks, no credit reporting, USD only, no real SMS or WhatsApp. Member
  messages appear in an in-app phone inbox.
- **PayPal wallet only.** Venmo payouts are cut: D1 test (d2) was not run (no sandbox phone), and the join flow
  rejects a Venmo wallet with 422.

## Custody

- The organizer's PayPal Business account (or, at scale, a CDFI or nonprofit) is the merchant of record. Members
  approve once on PayPal (Vault, `RECURRING_PREPAID`, with the billing plan shown on PayPal's approval page). Each
  round's contributions are captured into that account and paid out to the round's recipient by Payouts.
- Every circle has its own **net ledger** (`app/domain/ledger.py`). A payout must be covered by that circle's own
  ledger, whatever the account balance says.
- **The buffer is optional and disclosed.** It is an organizer-funded seed written in the charter (`buffer.seed`;
  $250 in the demo). It can cover one member's net share for a round, only through a charter-legal remedy that the
  circle votes on and the organizer approves, and it is repaid by PayPal invoice. A charter with a zero buffer works;
  remedies then fall back to a dated retry or a turn swap.
- **Fees are shown before consent**, on the charter card and the join page. Demo charter (`net_pot`): about $7.47
  per $200 contribution (3.49% + $0.49), and the Payouts API's flat $0.25 per payout comes out of the pot. Other
  fee policies (`gross_up`, `organizer_absorbs`) exist in the charter model.
- **Payout mandate.** Automatic payouts need an organizer-approved mandate: an HMAC signed by the server over the
  charter hash, recipient order and cap (`app/domain/mandate.py`). It is not a passkey or a PayPal-side mandate.

## Known gaps (as of 2026-10-23)

| Gap | Effect | Where |
|---|---|---|
| UNCLAIMED cancel fails in sandbox (400, then 500) | The app shows "Cancel not available in sandbox", re-routes with a new batch `r{rev+1}`, and shows the first item as **pending return**. The business account fronts the re-route; the buffer pays the extra $0.25 fee. "Pending return" clears only when a RETURNED/REFUNDED/CANCELED event reaches the webhook handler (`app/engine/webhooks.py` calls `rounds.mark_payout_item_returned`); real webhook delivery is verified (D1 f PASS 2026-10-26), but a RETURNED event has not been observed live. Risk: if the first item is **claimed** after the re-route went out, the member is paid twice and the business account loses the fronted amount; the SUCCEEDED webhook for that item marks the attempt "Claimed after re-route: pot paid twice" and raises an error toast for the organizer (recovering the extra amount is manual) | `app/engine/rounds.py`, `app/engine/webhooks.py` |
| PayPal Balances lags (~2.5 h in sandbox) | A reading older than 15 minutes is context only; the ledger is the binding check | `app/paypal/balances.py` |
| Webhook events live in `/tmp` on the free host | Real delivery and signature verification passed live (D1 f, 2026-10-26). A restart clears stored events; polling (payout status, `get_order_details`) stays as the backup. On an https deployment, unverified events are stored but never acted on | `app/engine/webhooks.py` |
| Judge mini-circle ($5, 2 members) | judge.member can do a real PayPal approval, but that circle has no payout mandate and no round runner yet, so it is never charged or paid | `app/routes/join.py` |
| Refunds | The wrapper exists; nothing calls it (no dissolve or void flow) | `app/paypal/refunds.py` |
| Toolkit coverage | 4 toolkit tools on the Judge Mode path, plus `get_invoice` from the member MCP (`get_my_repayment`, live-sandbox invoices only). `send_invoice_reminder` is wired behind an answer-scoped token that the flow does not issue once an invoice exists, so it is never called today; `list_transactions`, `list_disputes` are enabled but unused | `app/paypal/toolkit_adapter.py`, `app/engine/capabilities.py` |
| Remedy proposer with `PAYER_ACTION_REQUIRED` and an empty buffer | Fixed in M3 integration: no retry is proposed for a never-retry decline; the uncovered set is blocked and left to the organizer (eval s17). A dedicated "ask the organizer" prompt is still missing | `app/domain/remedies.py` |
| AI recordings cover Round 3 and recovery, not Round 4 | 68 recorded `gemini-3.5-flash-lite` turns ship (compile, forecast, Round 3, recovery, the s06 injection reply, Story B). The clean Round 4 collection was never recorded (every payout that day failed with `INSUFFICIENT_FUNDS`), so without a key Round 4 runs in labelled rules mode. A deploy must set `TK_LLM_MODEL=gemini-3.5-flash-lite` for the recordings to match | `app/fixtures/llm_cache.jsonl`, `docs/live-run/2026-11-03-takes.md` |
| Sandbox payouts need a funded Business account | The $1,154.93 pot payout returned `422 INSUFFICIENT_FUNDS` on 2026-11-03 although Balances still showed funds (it lags). The app says "Nothing was paid" and never claims the payout; the fix is a sandbox top-up. `/api/replay` shows a completed run | `app/engine/rounds.py`, `app/paypal/payouts.py` |
| Member passbook and member MCP | Demo circles only, behind `TK_PASSBOOK` and the member-agent flags. Tokens are server-minted HMAC capabilities, not member logins; a passbook link sent by WhatsApp is seen by WhatsApp. Replay and simulated rows are labelled "replay, no PayPal call" | `app/engine/passbook.py`, `app/engine/capabilities.py` |
| Interview baseline is empty | No organizer-minutes or messages-per-round figure is claimed. The persona "Priya" is a composite | `docs/sources.md` |
| Forecaster | Scored on a synthetic backtest only (precision 0.647, recall 0.647), never on real circles | `eval/RESULTS.md` |
| Simulated mode | With no sandbox credentials or no seeded vaults, Judge Mode simulates Round 3 and labels it; simulated rounds are never paid out | `app/engine/story.py` |
| One worker, SQLite | Fine for a demo with at most 6 concurrent visitors; not a production datastore | `app/db.py` |
