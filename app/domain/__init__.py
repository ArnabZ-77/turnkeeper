"""Pure domain logic for Turnkeeper: charter, billing plan, fees, ledger, mandate and policy.

Nothing in this package does I/O or reads settings. Money is ``decimal.Decimal`` with exactly two places
(see ``app.domain.fees.money``); the mandate secret is passed in by the caller.
"""
