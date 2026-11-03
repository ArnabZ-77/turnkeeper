"""Browser hardening headers for every response (pure ASGI, so SSE streaming is untouched).

CSP notes: the SPA loads only same-origin scripts, styles, fonts and images. The one inline script in
index.html (theme bootstrap) is allowed by hash, computed from the built file at startup so a rebuild never
needs a manual update. Bryntum sets inline styles, so style-src keeps 'unsafe-inline'; no script needs
'unsafe-eval'. TK_CSP_MODE=report-only ships the same policy without enforcing it (a rollback switch).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def inline_script_hashes(index_html: str) -> list[str]:
    """CSP source tokens ('sha256-...') for every inline <script> body in the page."""
    out = []
    for body in _INLINE_SCRIPT.findall(index_html):
        if body.strip():
            digest = hashlib.sha256(body.encode("utf-8")).digest()
            out.append(f"'sha256-{base64.b64encode(digest).decode()}'")
    return out


def build_csp(index_html: str = "") -> str:
    script_src = " ".join(["'self'", *inline_script_hashes(index_html)])
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {script_src}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "worker-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def _read_index() -> str:
    try:
        with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _is_https(scope: Scope) -> bool:
    if scope.get("scheme") in ("https", "wss"):
        return True
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-proto":
            return value.decode("latin-1").split(",")[0].strip().lower() == "https"
    return False


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._csp_cache: tuple[float, str] | None = None

    def _csp(self) -> str:
        try:
            mtime = os.path.getmtime(os.path.join(STATIC_DIR, "index.html"))
        except OSError:
            mtime = 0.0
        if self._csp_cache is None or self._csp_cache[0] != mtime:
            self._csp_cache = (mtime, build_csp(_read_index()))
        return self._csp_cache[1]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        https = _is_https(scope)
        path = scope.get("path", "")
        mode = (get_settings().csp_mode or "enforce").strip().lower()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in headers}

                def add(name: bytes, value: str) -> None:
                    if name not in present:
                        headers.append((name, value.encode("latin-1")))

                add(b"x-content-type-options", "nosniff")
                add(b"referrer-policy", "no-referrer")
                add(b"x-frame-options", "DENY")
                add(b"permissions-policy", "camera=(), microphone=(), geolocation=(), payment=()")
                add(b"cross-origin-opener-policy", "same-origin")
                if mode != "off":
                    name = (
                        b"content-security-policy-report-only"
                        if mode == "report-only"
                        else b"content-security-policy"
                    )
                    add(name, self._csp())
                if https:
                    add(b"strict-transport-security", "max-age=31536000")
                if path.startswith(("/api/", "/webhooks/")):
                    add(b"cache-control", "no-store")
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
