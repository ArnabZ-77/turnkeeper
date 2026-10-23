# Turnkeeper

**An AI treasurer for rotating savings circles (chit, susu, tanda, chama, stokvel), on PayPal sandbox rails.**

Each member approves once on PayPal. After that the agent collects every round, flags shortfalls early,
turns a declined payment into the fixes the circle's own rules allow, puts them to a member vote,
reschedules the circle, and pays the pot out, after checking the money is really there.

> Built for the PayPal AI Hackathon 2026. Everything runs against the **PayPal sandbox**; no real money moves.

_Status: under construction. The judge quickstart, API table, eval results and limitations will land here._

## Quickstart (local, replay mode)

PowerShell (x64 Python 3.11):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install "paypal-agent-toolkit==1.11.0" --no-deps
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # fill in PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (US sandbox app) and ANTHROPIC_API_KEY
.\.venv\Scripts\uvicorn app.main:app --port 8000
```

Then open http://localhost:8000/healthz.

## Seeding the demo members (`TK_SEED_VAULT_IDS`)

The demo circle's six members (`asha, ravi, meena, tomas, lin, dev`) need real **sandbox** vault ids before
round 3 can charge anyone. Approve each sandbox personal account once with `scripts/smoke/a_setup_token.py`,
then put the ids in `.env` (locally) or the Render env var of the same name, as one line of JSON:

```
TK_SEED_VAULT_IDS={"asha":"<vault id>","ravi":"<vault id>","meena":"<vault id>","tomas":"<vault id>","lin":"<vault id>","dev":"<vault id>"}
```

Each value may also be an object `{"vault_id", "customer_id", "payout_receiver", "payout_wallet"}`. Without it
every charge reads "No vault id on file", `/healthz` reports `"vault_seeded": false` with a warning, and
`GET /api/session` returns `"vault_seeded": false` so the board can say so.

## Deploying

`render.yaml` is a Render Blueprint. On an `https` `PUBLIC_BASE_URL` the app refuses to start while
`MANDATE_HMAC_SECRET` or `SESSION_SECRET` is the development default or shorter than 16 characters, and
`/api/debug/webhooks` requires `Authorization: Bearer <MCP_DEMO_TOKEN>`. `/healthz` lists missing configuration
under `warnings`.

## License

MIT, see [LICENSE](LICENSE).
