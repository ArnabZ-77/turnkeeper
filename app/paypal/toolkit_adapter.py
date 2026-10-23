"""Runs PayPal Agent Toolkit tools (paypal_agent_toolkit.shared) through our token cache, idempotency and gateway.

Why an adapter (research.md N2a): the toolkit's `PayPalClient` fetches a NEW OAuth token on every request (two
requests per tool call), uses the synchronous `requests` library, sends no `PayPal-Request-Id`, and a bare
`Context()` defaults to PRODUCTION. Here:

- `Context(sandbox=True)` is always passed explicitly and the client's base URL is checked against settings;
- `api._paypal_client` is replaced by `CachedTokenClient`, whose `get_access_token()` returns our cached token
  (pre-warmed with `await adapter.prepare()`; no `run_coroutine_threadsafe` from the worker thread) and whose
  request headers carry `PayPal-Request-Id` from a contextvar that `run()` sets for the duration of the call;
- the blocking call runs through `Gateway.run_sync`, so it is FIFO-ordered and counted in the same rate budget;
- the toolkit's JSON string is parsed to a dict and `requests` HTTP errors become `PayPalError`.
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Callable
from typing import Any

import requests
from paypal_agent_toolkit.shared.api import PayPalAPI
from paypal_agent_toolkit.shared.configuration import Configuration, Context, is_tool_allowed
from paypal_agent_toolkit.shared.paypal_client import PayPalClient as ToolkitClient
from paypal_agent_toolkit.shared.tools import tools as TOOLKIT_TOOLS

from app.config import Settings
from app.paypal.client import PayPalClient, PayPalError
from app.paypal.gateway import SYNC_METHOD, Gateway

log = logging.getLogger("turnkeeper.paypal.toolkit")

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tk_paypal_request_id", default=None
)

# The toolkit registry spells two invoice actions "generateQRC" and "sendReminder"; the contract's snake_case
# spellings are accepted too and normalised by `normalize_actions`, so both forms enable the tools.
ACTION_ALIASES: dict[str, dict[str, str]] = {
    "invoices": {"generate_qr_code": "generateQRC", "send_reminder": "sendReminder"},
}

DEFAULT_ACTIONS: dict[str, dict[str, bool]] = {
    "orders": {"get": True},
    "invoices": {"create": True, "send": True, "generate_qr_code": True, "send_reminder": True, "get": True},
    "disputes": {"list": True},
    "transactions": {"list": True},
}


def normalize_actions(actions: dict[str, dict[str, bool]]) -> dict[str, dict[str, bool]]:
    out: dict[str, dict[str, bool]] = {}
    for product, product_actions in actions.items():
        aliases = ACTION_ALIASES.get(product, {})
        out[product] = {aliases.get(action, action): bool(flag) for action, flag in product_actions.items()}
    return out


def parse_toolkit_result(raw: Any) -> dict:
    """The toolkit returns a JSON string; make it a dict (non-object JSON is wrapped under "result")."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes | bytearray):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"result": raw}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    return {"result": raw}


def _error_from_requests(e: requests.exceptions.HTTPError, method: str) -> PayPalError:
    resp = e.response
    status = int(resp.status_code) if resp is not None else 0
    body: dict = {}
    text = ""
    if resp is not None:
        try:
            parsed = resp.json()
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            text = (resp.text or "")[:300]
    return PayPalError(status, body, method=SYNC_METHOD, path=method, text=text)


class CachedTokenClient(ToolkitClient):
    """Toolkit client that reuses our cached OAuth token and adds PayPal-Request-Id from the run() contextvar."""

    def __init__(
        self, client_id: str, secret: str, context: Context, token_provider: Callable[[], str | None]
    ) -> None:
        super().__init__(client_id, secret, context)
        self._token_provider = token_provider

    def get_access_token(self) -> str:
        token = self._token_provider()
        if not token:
            raise RuntimeError(
                "no fresh PayPal token cached: await ToolkitAdapter.prepare() before running a tool"
            )
        return token

    def build_headers(self) -> dict:
        headers = super().build_headers()
        request_id = _request_id_var.get()
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        return headers


class ToolkitAdapter:
    """Runs PayPal Agent Toolkit tools through our token cache, idempotency keys and gateway."""

    def __init__(
        self,
        gw: Gateway,
        client: PayPalClient,
        actions: dict | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or client.settings
        self._gw = gw
        self._client = client
        self.context = Context(sandbox=True)
        self.config = Configuration(
            actions=normalize_actions(actions or DEFAULT_ACTIONS), context=self.context
        )
        self.api = PayPalAPI(settings.paypal_client_id, settings.paypal_client_secret, self.context)
        self.sync_client = CachedTokenClient(
            settings.paypal_client_id, settings.paypal_client_secret, self.context, client.cached_token
        )
        if self.sync_client.base_url != settings.paypal_base_url:
            raise RuntimeError(f"toolkit base url {self.sync_client.base_url} is not the sandbox")
        self.api._paypal_client = self.sync_client
        self._tools = [t for t in TOOLKIT_TOOLS if is_tool_allowed(t, self.config)]

    async def prepare(self) -> None:
        """Make sure a fresh token is cached for the sync client (refreshes when < 300 s remain)."""
        await self._client.get_token()

    def tool_names(self) -> list[str]:
        return [str(t["method"]) for t in self._tools]

    def tool_specs(self) -> list[dict]:
        """Registry entries for the enabled tools: method, name, description and the pydantic args_schema."""
        return [
            {
                "method": t["method"],
                "name": t["name"],
                "description": t["description"],
                "args_schema": t["args_schema"],
            }
            for t in self._tools
        ]

    def _invoker(self, method: str, params: dict, request_id: str | None) -> Callable[[], Any]:
        def _invoke() -> Any:
            token = _request_id_var.set(request_id)
            try:
                return self.api.run(method, dict(params or {}))
            except requests.exceptions.HTTPError as e:
                raise _error_from_requests(e, method) from e
            except requests.exceptions.RequestException as e:
                raise PayPalError(
                    0,
                    {"name": "TRANSPORT_ERROR", "message": f"{type(e).__name__}: {e}"},
                    method=SYNC_METHOD,
                    path=method,
                ) from e
            finally:
                _request_id_var.reset(token)

        return _invoke

    async def run(self, method: str, params: dict, *, request_id: str | None = None) -> dict:
        """Execute `api.run(method, params)` via the gateway (FIFO + rate budget) and return the parsed result."""
        if method not in self.tool_names():
            raise ValueError(f"toolkit tool {method!r} is not enabled; enabled: {self.tool_names()}")
        await self.prepare()
        try:
            raw = await self._gw.run_sync(
                self._invoker(method, params, request_id), method=SYNC_METHOD, path=method
            )
        except PayPalError as e:
            if e.status != 401:
                raise
            log.info("toolkit %s -> 401; refreshing token and retrying once", method)
            await self._client.get_token(force=True)
            raw = await self._gw.run_sync(
                self._invoker(method, params, request_id), method=SYNC_METHOD, path=method
            )
        return parse_toolkit_result(raw)

    async def order_details(self, order_id: str) -> dict:
        """toolkit `get_order_details` -> the raw order dict (its `status` and captures confirm a charge)."""
        result = await self.run("get_order_details", {"order_id": order_id})
        raw = result.get("raw")
        return raw if isinstance(raw, dict) else result
