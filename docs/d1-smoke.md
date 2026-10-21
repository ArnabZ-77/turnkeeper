# D1 sandbox smoke tests (go/no-go)

Run each script from the repo root with a filled `.env` (US sandbox Business app):
`.\.venv\Scripts\python scripts\smoke\<script>.py`. Record the result here, verbatim where useful.
Nothing in `app/` is trusted until the row it depends on is PASS or has a chosen fallback.

Precondition: **Country = US for the business account, the REST app, and every personal account.** [ ]

| # | Test | Script | Result | Chosen path / fallback |
|---|---|---|---|---|
| a | Setup token (RECURRING_PREPAID + billing_plan) → approve → payment token (`vault_id`) | `a_setup_token.py` | | |
| a2 | PayPal approval page shows amount / frequency / cycle count (screenshot in docs/screenshots/) | manual | | Line A or Line B in the video |
| b | Merchant-initiated CAPTURE on `vault_id`; record `paypal_fee` and `net_amount` | `b_mit_capture.py` | | fee = |
| c | `PayPal-Mock-Response` INSTRUMENT_DECLINED on a vault order (expect 4xx, no order) | `c_mock_decline.py` | | |
| d | Payouts to PAYPAL email; UNCLAIMED to a no-account email → cancel; reuse `sender_batch_id` → dedupe; record payout fee | `d_payouts.py` | | fee = |
| d2 | Payouts to VENMO (phone of a US sandbox personal) | `d_payouts.py --venmo` | | keep / cut |
| e | `GET /v1/reporting/balances` freshness right after (b) | `e_balances.py` | | |
| f | Real webhook to the public URL, signature verified | `f_webhook.py` | | |
| g | Toolkit token-injection + `get_order_details` on (b) | `g_toolkit_wrap.py` | | |
| h | Bryntum trial expiry behaviour (Discord answer + package inspection) | manual | | |

Key length limits observed: `PayPal-Request-Id` ≤ ___ · `sender_batch_id` ≤ ___ · `sender_item_id` ≤ ___

Decision log: see `docs/decisions.md`.
