# Decision log

Dated, one line each, newest first. Cuts and fallbacks chosen at the D1 go/no-go and the Oct 25 / Oct 29 checkpoints go here.

- 2026-10-21 · Repo lives at `turnkeeper/` as its own git root; the research and plan docs stay outside the public repo.
- 2026-10-21 · PayPal Agent Toolkit installed with `--no-deps`; only `paypal_agent_toolkit.shared.*` is used (its framework adapters have broken pins).
- 2026-10-21 · Sponsor target: Bryntum Scheduler (not AG Grid). APIMatic plugin used at build time for extra consideration.
- 2026-10-21 · Reasoning model `claude-opus-5-5`, text model `claude-haiku-5-5`; no vector store / RAG in the MVP.
- 2026-10-21 · All sandbox accounts must be **US**; the default India accounts are never used.
- 2026-10-22 · Bryntum trial: the 45-day clock is per visitor browser from the first load of a bundle's install-time UUID; every `npm ci` (every Render Docker build) resets it. Final production image must be built on/after Nov 2. Pin `@bryntum/scheduler-trial@7.3.7` + `@bryntum/scheduler-react@7.3.7`.
