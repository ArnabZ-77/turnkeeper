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

## License

MIT, see [LICENSE](LICENSE).
