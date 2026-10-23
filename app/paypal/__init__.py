"""PayPal sandbox integration (B1).

- `client`   : httpx AsyncClient with an OAuth token cache, retries and typed errors.
- `gateway`  : one FIFO worker + rolling-window rate limit in front of the client (shared by raw REST calls
               and the Agent Toolkit adapter).
- `keys`     : run-scoped idempotency keys (`PayPal-Request-Id`, `sender_batch_id`, ...).
- `vault`, `orders`, `payouts`, `balances`, `refunds`, `webhooks`: thin typed wrappers returning response dicts.
- `toolkit_adapter`: runs paypal_agent_toolkit tools through the same token cache, request ids and gateway.

Everything here talks to the sandbox only: the base URL comes from `app.config.get_settings().paypal_base_url`.
"""

from app.paypal.client import PayPalClient, PayPalError, PayPalResponse
from app.paypal.gateway import Gateway, get_gateway

__all__ = ["Gateway", "PayPalClient", "PayPalError", "PayPalResponse", "get_gateway"]
