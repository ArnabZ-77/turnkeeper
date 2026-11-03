# PayPal API table

Every PayPal call Turnkeeper makes, the file that makes it, and what the real US sandbox did on the D1 smoke
tests (2026-10-23, `docs/d1-smoke.md`). All calls go to `https://api-m.sandbox.paypal.com` only:
`app/config.py` refuses to start with any `PAYPAL_ENV` other than `sandbox`.

Every call, toolkit or REST, goes through one gateway ([`app/paypal/gateway.py`](../app/paypal/gateway.py)):
a single FIFO worker, a token bucket (40/min by default, 300 ms minimum gap), one cached OAuth token
([`app/paypal/client.py`](../app/paypal/client.py)), retries only on 401/429/5xx, and a `PayPal-Request-Id` on every
POST built from the run id ([`app/paypal/keys.py`](../app/paypal/keys.py)), so a retried step can never charge or pay
twice. Each call is logged to the `paypal_calls` table.

## Direct REST

| # | Product | Endpoint | Wrapper | Called from (app path) | Sandbox result (D1) |
|---|---|---|---|---|---|
| 1 | OAuth | `POST /v1/oauth2/token` | [`client.py`](../app/paypal/client.py) | every call (cached, refreshed < 300 s before expiry) | PASS |
| 2 | Vault v3 | `POST /v3/vault/setup-tokens` (`usage_pattern: RECURRING_PREPAID` + `billing_plan`) | [`vault.py`](../app/paypal/vault.py) `create_setup_token` | [`app/routes/join.py`](../app/routes/join.py) `/api/join/{invite}/start` | PASS (a). The plan needs `one_time_charges` and `pricing_model: VARIABLE`; `FIXED` returned 422 |
| 3 | Vault v3 | `GET /v3/vault/setup-tokens/{id}` | `vault.get_setup_token` | `join.py` return URL (approved / action required) | PASS (a) |
| 4 | Vault v3 | `POST /v3/vault/payment-tokens` | `vault.create_payment_token` | `join.py` return URL: stores `vault_id` + `customer_id` | PASS (a) |
| 5 | Vault v3 | `GET /v3/vault/payment-tokens?customer_id=` | `vault.list_payment_tokens` | smoke script only (`scripts/smoke/a_setup_token.py --list`) | PASS |
| 6 | Orders v2 | `POST /v2/checkout/orders` (`intent: CAPTURE`, `payment_source.paypal.vault_id`, no buyer present) | [`orders.py`](../app/paypal/orders.py) `charge_vaulted` | [`app/engine/rounds.py`](../app/engine/rounds.py) `charge_member` | PASS (b): COMPLETED at once; $1.00 → fee 0.52, net 0.48 |
| 7 | Orders v2 | same, with `PayPal-Mock-Response: {"mock_application_codes":"INSTRUMENT_DECLINED"}` | `charge_vaulted(mock_decline=True)` | `rounds.charge_member` when the Judge flag `ravi_declines` is on | PASS (c): 422 `INSTRUMENT_DECLINED`, no order. Shown as **"API decline: sandbox-injected"** |
| 8 | Payouts v1 | `POST /v1/payments/payouts` (`sender_batch_id = tk:{run}:{circle}:{round}:r{rev}`) | [`payouts.py`](../app/paypal/payouts.py) `create_payout` | `rounds.pay_round`, `rounds.reroute_unclaimed` | PASS (d): SUCCESS in ~18 s, item fee $0.25. Re-posting a batch id → 400 (dedupe works) |
| 9 | Payouts v1 | `GET /v1/payments/payouts/{batch_id}` | `get_payout_batch`, `wait_for_batch_final` | `rounds.pay_round`, `rounds.reroute_unclaimed` | PASS (d) |
| 10 | Payouts v1 | `GET /v1/payments/payouts-item/{item_id}` | `get_payout_item` | `rounds.payout_status`, in-app receipt | PASS (d) |
| 11 | Payouts v1 | `POST /v1/payments/payouts-item/{item_id}/cancel` | `cancel_payout_item` | `rounds.reroute_unclaimed` (tried exactly once) | **Fails in sandbox** (d): 400 `BATCH_NOT_COMPLETED` while PROCESSING, then 500 after SUCCESS. The app shows "Cancel not available in sandbox" and re-routes as a new batch `r{rev+1}` |
| 12 | Reporting | `GET /v1/reporting/balances?currency_code=USD` | [`balances.py`](../app/paypal/balances.py) `get_balances`, `balance_context` | `rounds.available_balance`, `rounds.pay_round` | **Lags** (e): `last_refresh_time` ~2.5 h old. Shown as context ("last refreshed …"); a reading older than 15 min never blocks a payout; the per-circle ledger is the binding check |
| 13 | Webhooks | `POST /v1/notifications/verify-webhook-signature` | [`webhooks.py`](../app/paypal/webhooks.py) `verify_signature` | [`app/engine/webhooks.py`](../app/engine/webhooks.py) `handle_event` | **PASS** 2026-10-26 on the Render host: real `PAYMENT.CAPTURE.COMPLETED`, `verified: true` (f) |
| 14 | Webhooks | `POST /v1/notifications/webhooks`, `GET`, `DELETE` | `register_webhook` and friends | [`scripts/register_webhook.py`](../scripts/register_webhook.py) (operator script) | Not yet run (f) |

Webhook events the reconciler handles ([`app/engine/webhooks.py`](../app/engine/webhooks.py)):
`PAYMENT.CAPTURE.COMPLETED` / `DENIED`, `PAYMENT.PAYOUTS-ITEM.*`, `PAYMENT.PAYOUTSBATCH.SUCCESS`,
`VAULT.PAYMENT-TOKEN.*` (a deleted token stops further charges), `INVOICING.INVOICE.PAID` (repays the buffer
advance). Events are deduplicated on the event id; on an https deployment an unverified event is stored but never
acted on.

Wrappers that exist but are **not wired into the app**: `refunds.refund_capture` / `GET /v2/payments/refunds/{id}`
([`refunds.py`](../app/paypal/refunds.py)), `vault.delete_payment_token`, `orders.capture_order` and
`orders.create_vault_with_purchase_order` (a join fallback that D1 made unnecessary).

## PayPal Agent Toolkit (`paypal-agent-toolkit==1.11.0`, `paypal_agent_toolkit.shared` only)

[`app/paypal/toolkit_adapter.py`](../app/paypal/toolkit_adapter.py) runs toolkit tools through the same gateway. Its
toolkit client reuses our cached OAuth token (the stock client fetches a token per call) and adds a
`PayPal-Request-Id` from a context variable (the stock client sends none). D1 (g): PASS, one token fetch shared by
the toolkit and REST.

| Toolkit tool | Called from | Purpose |
|---|---|---|
| `get_order_details` | `rounds.confirm_capture` (MCP tool `get_order_details`) | confirms each capture after the charge |
| `create_invoice` | [`app/engine/recovery.py`](../app/engine/recovery.py) `repay_by_invoice` | the short member repays the buffer advance |
| `send_invoice` | `recovery.py` | sends that invoice |
| `generate_invoice_qr_code` | `recovery.py` | QR code shown on the member's phone inbox |
| `get_invoice` | [`app/engine/capabilities.py`](../app/engine/capabilities.py) `repayment` (public MCP `get_my_repayment`) | the member's own assistant reads the invoice status (live-sandbox invoices only) |
| `send_invoice_reminder` | `capabilities.repayment`, answer-scoped token only | PayPal's own reminder, claimed once in the database before the send |

**Honest count: 4 toolkit tools are on the Judge Mode path**, and a fifth (`get_invoice`) runs when a member's
assistant calls `get_my_repayment` on a live-sandbox invoice. `send_invoice_reminder` is wired, but only an
answer-scoped member token may send it, and the current flow issues no such token once an invoice exists, so no
call reaches it today (a read token reports the reminder as `due`). `list_disputes` and `list_transactions` are
enabled and unused. Vault, Orders create, Payouts,
Balances and Webhooks are direct REST: the Python toolkit has no Vault, Payouts or Webhooks tools, and the
merchant-initiated charge needs `payment_source.paypal.vault_id`, the sandbox mock header and our request id.

Without sandbox credentials the invoice is created as `SIM-INV-…` and labelled **simulated**.
