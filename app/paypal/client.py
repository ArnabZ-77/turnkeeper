"""Async PayPal REST client: OAuth token cache, retries and typed errors. Sandbox only.

Behaviour (see docs/CONTRACTS.md, B1):
- `get_token()` posts `grant_type=client_credentials` with HTTP basic auth and caches the token in memory until
  `expires_in - 300 s`. An `asyncio.Lock` serialises refreshes so concurrent callers share one fetch.
- `request()` adds `Authorization`, `Content-Type: application/json`, `Accept`, `Prefer: return=representation`
  and `PayPal-Request-Id` (when given). One automatic retry with a fresh token on 401. 429 and 5xx are retried
  with 1/2/4 s backoff (max 3 retries); any other 4xx (a business error such as INSTRUMENT_DECLINED) is never
  retried. A final non-2xx raises `PayPalError`. A 204 or empty body yields `data == {}`.
- Tokens and secrets are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import Settings, get_settings

log = logging.getLogger("turnkeeper.paypal.client")

TOKEN_PATH = "/v1/oauth2/token"
TOKEN_REFRESH_MARGIN_S = 300
RETRY_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0)
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "DELETE", "PUT"})


class PayPalError(Exception):
    """A final non-2xx answer from PayPal (after retries), a transport failure, or a failed token fetch.

    `name` is PayPal's error name (e.g. "UNPROCESSABLE_ENTITY"); `details[].issue` carries codes such as
    "INSTRUMENT_DECLINED" (see `issue_codes`). `status == 0` means the request never got an HTTP answer.
    """

    def __init__(
        self,
        status: int,
        raw: dict | None = None,
        *,
        method: str = "",
        path: str = "",
        text: str = "",
    ) -> None:
        self.status: int = int(status)
        self.raw: dict = raw if isinstance(raw, dict) else {}
        self.name: str = str(self.raw.get("name") or self.raw.get("error") or f"HTTP_{self.status}")
        self.message: str = str(self.raw.get("message") or self.raw.get("error_description") or text or "")
        debug_id = self.raw.get("debug_id")
        self.debug_id: str | None = str(debug_id) if debug_id else None
        details = self.raw.get("details")
        self.details: list[dict] = [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []
        self.method = method
        self.path = path
        super().__init__(
            f"{method} {path} -> {self.status} {self.name}: {self.message} "
            f"(debug_id={self.debug_id}, issues={self.issue_codes})"
        )

    @property
    def issue_codes(self) -> list[str]:
        return [str(d["issue"]) for d in self.details if d.get("issue")]

    def has_issue(self, code: str) -> bool:
        return code in self.issue_codes

    @property
    def retryable(self) -> bool:
        return self.status == 429 or self.status >= 500

    def to_dict(self) -> dict:
        """Safe, token-free summary for tool results, traces and logs."""
        return {
            "status": self.status,
            "name": self.name,
            "message": self.message,
            "debug_id": self.debug_id,
            "issues": self.issue_codes,
        }


class PayPalResponse(BaseModel):
    status: int
    data: dict = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


def parse_body(resp: httpx.Response) -> dict:
    """JSON body as a dict. 204/empty -> {}. Non-JSON -> {"_text": ...}. Non-object JSON -> {"_value": ...}."""
    if resp.status_code == 204 or not resp.content:
        return {}
    try:
        parsed = resp.json()
    except ValueError:
        return {"_text": resp.text[:2000]}
    if isinstance(parsed, dict):
        return parsed
    return {"_value": parsed}


class PayPalClient:
    """httpx-based client with a shared OAuth token cache. One instance per process is enough."""

    def __init__(self, settings: Settings | None = None, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self.base_url: str = self._settings.paypal_base_url
        self._http = http or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        self._owns_http = http is None
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()
        # Injection points for tests (fake clock / no real sleeping).
        self._sleep = asyncio.sleep
        self._clock = time.monotonic
        self.token_fetches = 0

    @property
    def settings(self) -> Settings:
        return self._settings

    # ----------------------------------------------------------------------------- token cache

    def cached_token(self) -> str | None:
        """The current token if it is still fresh (sync; used by the toolkit adapter's sync bridge)."""
        if self._token and self._clock() < self._token_expires_at:
            return self._token
        return None

    async def get_token(self, force: bool = False) -> str:
        async with self._lock:
            if not force:
                cached = self.cached_token()
                if cached:
                    return cached
            return await self._fetch_token()

    async def _refresh_after_401(self, stale: str) -> str:
        """Refresh once after a 401, unless another caller already replaced the stale token."""
        async with self._lock:
            cached = self.cached_token()
            if cached and cached != stale:
                return cached
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        url = f"{self.base_url}{TOKEN_PATH}"
        retries = 0
        while True:
            self.token_fetches += 1
            try:
                resp = await self._http.post(
                    url,
                    data={"grant_type": "client_credentials"},
                    auth=(self._settings.paypal_client_id, self._settings.paypal_client_secret),
                    headers={"Accept": "application/json", "Accept-Language": "en_US"},
                )
            except httpx.HTTPError as e:
                if retries < len(RETRY_BACKOFF_S):
                    delay = RETRY_BACKOFF_S[retries]
                    retries += 1
                    log.warning("token fetch transport error (%s); retry in %.0fs", type(e).__name__, delay)
                    await self._sleep(delay)
                    continue
                raise PayPalError(
                    0, {"name": "TRANSPORT_ERROR", "message": f"{type(e).__name__}: {e}"}, method="POST", path=TOKEN_PATH
                ) from e
            status = resp.status_code
            if (status == 429 or status >= 500) and retries < len(RETRY_BACKOFF_S):
                delay = RETRY_BACKOFF_S[retries]
                retries += 1
                log.warning("token fetch -> %s; retry %d/%d in %.0fs", status, retries, len(RETRY_BACKOFF_S), delay)
                await self._sleep(delay)
                continue
            body = parse_body(resp)
            if status != 200:
                raise PayPalError(status, body, method="POST", path=TOKEN_PATH)
            token = body.get("access_token")
            if not token:
                raise PayPalError(
                    status, {"name": "NO_ACCESS_TOKEN", "message": "token response had no access_token"},
                    method="POST", path=TOKEN_PATH,
                )
            try:
                expires_in = int(body.get("expires_in") or 0)
            except (TypeError, ValueError):
                expires_in = 0
            ttl = max(expires_in - TOKEN_REFRESH_MARGIN_S, expires_in // 2, 0)
            self._token = str(token)
            self._token_expires_at = self._clock() + ttl
            log.info("paypal token refreshed (expires_in=%ss, cached for %ss)", expires_in, ttl)
            return self._token

    # ----------------------------------------------------------------------------- requests

    @staticmethod
    def _build_headers(token: str, headers: dict | None, request_id: str | None) -> dict[str, str]:
        h: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            h["PayPal-Request-Id"] = request_id
        if headers:
            h.update({str(k): str(v) for k, v in headers.items()})
        return h

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        request_id: str | None = None,
    ) -> PayPalResponse:
        method = method.upper()
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.base_url}{path}"
        token = await self.get_token()
        retries = 0
        refreshed = False
        while True:
            hdrs = self._build_headers(token, headers, request_id)
            started = self._clock()
            try:
                resp = await self._http.request(method, url, json=json, params=params, headers=hdrs)
            except httpx.HTTPError as e:
                # Only retry when a repeat cannot double-apply: safe methods, or a POST with an idempotency key.
                safe = method in IDEMPOTENT_METHODS or bool(request_id)
                if safe and retries < len(RETRY_BACKOFF_S):
                    delay = RETRY_BACKOFF_S[retries]
                    retries += 1
                    log.warning("%s %s transport error (%s); retry in %.0fs", method, path, type(e).__name__, delay)
                    await self._sleep(delay)
                    continue
                raise PayPalError(
                    0, {"name": "TRANSPORT_ERROR", "message": f"{type(e).__name__}: {e}"}, method=method, path=path
                ) from e
            status = resp.status_code
            ms = int((self._clock() - started) * 1000)
            if status == 401 and not refreshed:
                refreshed = True
                log.info("%s %s -> 401; refreshing token once", method, path)
                token = await self._refresh_after_401(token)
                continue
            if (status == 429 or status >= 500) and retries < len(RETRY_BACKOFF_S):
                delay = RETRY_BACKOFF_S[retries]
                retries += 1
                log.warning(
                    "%s %s -> %s; retry %d/%d in %.0fs (retry-after=%s)",
                    method, path, status, retries, len(RETRY_BACKOFF_S), delay, resp.headers.get("Retry-After"),
                )
                await self._sleep(delay)
                continue
            data = parse_body(resp)
            if status >= 400:
                err = PayPalError(status, data, method=method, path=path, text="" if data else resp.text[:300])
                log.warning(
                    "%s %s -> %s %s debug_id=%s issues=%s ms=%d",
                    method, path, status, err.name, err.debug_id, err.issue_codes, ms,
                )
                raise err
            log.debug("%s %s -> %s ms=%d request_id=%s", method, path, status, ms, request_id)
            return PayPalResponse(status=status, data=data, headers={k: v for k, v in resp.headers.items()})

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> PayPalClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
