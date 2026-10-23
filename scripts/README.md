# scripts/ — D1 sandbox smoke tests and the webhook registrar

Run from anywhere with the venv interpreter, `-I` safe: `.venv\Scripts\python -I scripts\smoke\<name>.py --help`.
Each script reads `.env` through `app.config.get_settings()` (sandbox only), prints one JSON line per step and a
final `PASS`/`FAIL` (exit 0/1; 2 = missing credentials or bad arguments). Tokens and secrets are never printed.
Order: `a_setup_token` (prints the approve link, waits for Enter, prints `vault_id`) -> `b_mit_capture --vault-id`
(prints `order_id`, fee, net) -> `c_mock_decline --vault-id` -> `d_payouts --receiver <email> [--venmo +1555...] [--unclaimed]`
-> `e_balances` -> `f_webhook --public-url https://... [--vault-id ...]` -> `g_toolkit_wrap --order-id`.
`register_webhook.py [--url] | --list | --delete <id>` registers the listener once per public URL and prints the id to
put in `.env` as `PAYPAL_WEBHOOK_ID` (it never edits `.env`). Nothing here runs under pytest; it hits the real sandbox.
Record every result (PASS/FAIL, fees, key length limits, chosen fallback) in `docs/d1-smoke.md`.
