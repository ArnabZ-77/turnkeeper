"""Bearer-token auth, per-token+IP rate limit and the mount for the public MCP endpoint.

``mount_public_mcp(app)`` serves ``paypal_ops_public.http_app(path="/mcp")`` at exactly ``/mcp`` (stateless
streamable-HTTP with JSON responses, so ``curl`` and any MCP client work) and chains the MCP app's lifespan
onto the FastAPI app's lifespan so the session manager exists when requests arrive.

Why a ``Route`` and not ``app.mount``: a Starlette ``Mount("/mcp", ...)`` only matches ``/mcp/...`` and
answers ``POST /mcp`` with a 307 redirect that MCP clients do not follow; a ``Route("/mcp", endpoint=asgi_app)``
matches the exact path and hands the scope to the MCP Starlette app, whose own router serves ``/mcp``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from starlette.middleware import Middleware
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings

log = logging.getLogger("turnkeeper.mcp.auth")

RATE_LIMIT_PER_MIN = 30
RATE_WINDOW_SECONDS = 60.0
PUBLIC_MCP_PATH = "/mcp"
TOKEN_NOT_CONFIGURED = "MCP demo token not configured"


class RateLimiter:
    """Sliding-window counter: at most ``limit`` hits per ``window`` seconds per key. Thread-safe."""

    def __init__(
        self, limit: int = RATE_LIMIT_PER_MIN, window: float = RATE_WINDOW_SECONDS, clock=time.monotonic
    ):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Record a hit for ``key``. Returns ``(allowed, retry_after_seconds)``."""
        now = self._clock()
        with self._lock:
            if len(self._hits) > 2048:
                self._evict(now)
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(0.0, self.window - (now - q[0]))
            q.append(now)
            return True, 0.0

    def _evict(self, now: float) -> None:
        stale = [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]
        for k in stale:
            del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(scope: Scope) -> str:
    """Best-effort caller IP: first ``X-Forwarded-For`` hop (Render fronts the app) else the socket peer."""
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    return client[0] if client else "unknown"


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


class BearerAuthMiddleware:
    """Pure-ASGI middleware: ``Authorization: Bearer <settings.mcp_demo_token>`` or 401.

    - token setting empty -> 503 ``{"error": "MCP demo token not configured"}`` for every request
    - more than ``limit`` requests per minute per (presented token, IP) -> 429 with ``Retry-After``
    - wrong or missing token -> 401 with ``WWW-Authenticate: Bearer``
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token_getter: Callable[[], str] | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self._token_getter = token_getter or (lambda: get_settings().mcp_demo_token)
        self.limiter = limiter or RateLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        expected = (self._token_getter() or "").strip()
        if not expected:
            await _json_response(send, 503, {"ok": False, "error": TOKEN_NOT_CONFIGURED})
            return
        presented = _bearer_token(scope)
        key = f"{_token_fingerprint(presented)}|{client_ip(scope)}"
        allowed, retry_after = self.limiter.check(key)
        if not allowed:
            await _json_response(
                send,
                429,
                {"ok": False, "error": "rate limited: 30 requests per minute per token and IP"},
                extra_headers=[(b"retry-after", str(int(retry_after) + 1).encode())],
            )
            return
        if not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
            await _json_response(
                send,
                401,
                {"ok": False, "error": "unauthorized: send Authorization: Bearer <MCP demo token>"},
                extra_headers=[(b"www-authenticate", b'Bearer realm="turnkeeper-mcp"')],
            )
            return
        await self.app(scope, receive, send)


def _bearer_token(scope: Scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            raw = value.decode("latin-1").strip()
            scheme, _, token = raw.partition(" ")
            if scheme.lower() == "bearer":
                return token.strip()
            return ""
    return ""


async def _json_response(
    send: Send, status: int, body: dict[str, Any], extra_headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    headers.extend(extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def build_public_mcp_app(path: str = PUBLIC_MCP_PATH, *, limiter: RateLimiter | None = None) -> Any:
    """The Starlette app for the public server with auth + rate limit middleware (stateless, JSON)."""
    from app.mcp.public import paypal_ops_public

    middleware = [Middleware(BearerAuthMiddleware, limiter=limiter)]
    return paypal_ops_public.http_app(
        path=path, middleware=middleware, stateless_http=True, json_response=True
    )


def mount_public_mcp(app: Any, *, path: str = PUBLIC_MCP_PATH) -> Any:
    """Serve the public MCP server at ``path`` on ``app`` (FastAPI). Idempotent. Returns the MCP ASGI app.

    Called by ``app/main.py`` inside ``_include_routers`` (B4). Must run before any catch-all SPA route is
    added, because Starlette matches routes in order.
    """
    state = app.state
    existing = getattr(state, "public_mcp_app", None)
    if existing is not None:
        return existing
    mcp_app = build_public_mcp_app(path)
    app.router.routes.append(Route(path, endpoint=mcp_app, methods=["GET", "POST", "DELETE"]))
    _chain_lifespan(app, mcp_app)
    state.public_mcp_app = mcp_app
    log.info("public MCP mounted at %s", path)
    return mcp_app


def _chain_lifespan(app: Any, mcp_app: Any) -> None:
    router = app.router
    original = router.lifespan_context

    @asynccontextmanager
    async def chained(app_: Any):
        async with original(app_) as state:
            async with mcp_app.lifespan(app_):
                yield state

    router.lifespan_context = chained
