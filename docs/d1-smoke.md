# D1 sandbox smoke tests (go/no-go)

Run each script from the repo root with a filled `.env` (US sandbox Business app):
`.\.venv\Scripts\python scripts\smoke\<script>.py`. Record the result here, verbatim where useful.
Nothing in `app/` is trusted until the row it depends on is PASS or has a chosen fallback.

Precondition: **Country = US for the business account, the REST app, and every personal account.** [x] (USD balance; vault + MIT capture succeeded)

| # | Test | Script | Result | Chosen path / fallback |
|---|---|---|---|---|
| a | Setup token (RECURRING_PREPAID + billing_plan) → approve → payment token (`vault_id`) | `a_setup_token.py` | **PASS** 2026-10-23: setup token APPROVED → payment token created, usage_pattern RECURRING_PREPAID. Billing plan needed `one_time_charges` and `pricing_model: VARIABLE` (FIXED → 422 INCOMPATIBLE_PARAMETER_VALUE). Note: payment token response reports `has_billing_plan: false`. | billing_plan.py fixed |
| a2 | PayPal approval page shows amount / frequency / cycle count (screenshot in docs/screenshots/) | manual | **PASS** (user-confirmed 2026-10-23: the approval page shows the plan). Screenshot to add as docs/screenshots/consent.png. | **Line A** in the video |
| b | Merchant-initiated CAPTURE on `vault_id`; record `paypal_fee` and `net_amount` | `b_mit_capture.py` | **PASS**: $1.00 order COMPLETED immediately with capture, no buyer present. fee 0.52, net 0.48 (= 3.49% + $0.49). | fee = 3.49% + 0.49 confirmed |
| c | `PayPal-Mock-Response` INSTRUMENT_DECLINED on a vault order (expect 4xx, no order) | `c_mock_decline.py` | **PASS**: 422 UNPROCESSABLE_ENTITY, issue INSTRUMENT_DECLINED, no order created. | label "API decline: sandbox-injected" |
| d | Payouts to PAYPAL email; UNCLAIMED to a no-account email → cancel; reuse `sender_batch_id` → dedupe; record payout fee | `d_payouts.py` | **PASS with one sandbox limitation**: payout to a sandbox personal email SUCCESS in ~18 s, item fee $0.25 (= Payouts API flat fee). Re-posting the same `sender_batch_id` → 400 "Batch with given sender_batch_id already exists" (dedupe works). UNCLAIMED reproduced with a no-account email. **Cancel of the UNCLAIMED item fails in sandbox**: 400 BATCH_NOT_COMPLETED while the batch is PROCESSING, then 500 INTERNAL_SERVICE_ERROR (twice) after the batch is SUCCESS. | payout fee reserve = 0.25; UNCLAIMED path = show UNCLAIMED + re-route as a NEW batch (`r{rev+1}`) to the other wallet; never claim the cancel succeeded |
| d2 | Payouts to VENMO (phone of a US sandbox personal) | `d_payouts.py --venmo` | NOT RUN (no sandbox phone configured) | **cut by default** (PLAN §8); PayPal wallet only |
| e | `GET /v1/reporting/balances` freshness right after (b) | `e_balances.py` | **LAGS**: available 5000.00 with last_refresh_time ~2.5 h old; the $0.48 capture not reflected. | Ledger is the binding check; Balances shown as "PayPal balance (last refreshed …)" |
| f | Real webhook to the public URL, signature verified | `f_webhook.py` | **PASS** 2026-10-26 on the Render free host (https://turnkeeper-s8xv.onrender.com): a real `PAYMENT.CAPTURE.COMPLETED` for a $1 vault capture arrived ~20 s after the capture with `verified: true` (PayPal verify-webhook-signature). Note: the free plan keeps SQLite in /tmp, so a restart clears stored events. | webhooks verified in production; polling stays as the backup |
| g | Toolkit token-injection + `get_order_details` on (b) | `g_toolkit_wrap.py` | **PASS**: toolkit get_order_details returned COMPLETED + capture id; one token fetch shared by toolkit and REST. | toolkit on the critical path |
| h | Bryntum trial expiry behaviour (Discord answer + package inspection) | manual | | |

Key length limits observed: `PayPal-Request-Id` ≤ ___ · `sender_batch_id` ≤ ___ · `sender_item_id` ≤ ___

Decision log: see `docs/decisions.md`.
