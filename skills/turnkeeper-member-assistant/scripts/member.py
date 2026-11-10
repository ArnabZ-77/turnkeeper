#!/usr/bin/env python3
"""Turnkeeper member assistant: read the request, answer it once, check the repayment. Standard library only.

    TURNKEEPER_CAP='tkc1...' python3 member.py request   [--base URL]
    TURNKEEPER_CAP='tkc1...' python3 member.py answer --choice yes|no [--rationale TEXT] [--base URL]
    TURNKEEPER_CAP='tkc1...' python3 member.py repayment [--base URL]

The token goes in the X-Turnkeeper-Capability header (never the URL). A passbook link (https://host/m#tkc1...) is
accepted too: the part after '#' is the token, and the link's own scheme://host is where it is sent (a different
--base is refused). The base must be https (http only for localhost / 127.0.0.1), and redirects are refused, so the
token never reaches another host. Prints one JSON object; exit 0 on success, 2 on a named refusal, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://turnkeeper-s8xv.onrender.com"
HEADER = "X-Turnkeeper-Capability"
LOCAL_HOSTS = ("localhost", "127.0.0.1")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies every header (the capability too) onto a redirect, even to another host: never follow one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None  # urllib then raises HTTPError with the 30x code


_OPENER = urllib.request.build_opener(_NoRedirect)


def token_from(raw: str) -> str:
    raw = (raw or "").strip()
    if "#" in raw:
        raw = raw.split("#", 1)[1]
    if not raw.startswith("tkc1."):
        raise SystemExit("not a Turnkeeper member token (it starts with tkc1.)")
    return raw


def link_base(raw: str) -> str | None:
    """The scheme://host a passbook link came from, or None when the input is a bare token."""
    raw = (raw or "").strip()
    head = raw.split("#", 1)[0] if "#" in raw else ""
    if not head:
        return None
    url = urllib.parse.urlsplit(head)
    if not (url.scheme and url.netloc):
        raise SystemExit("not a passbook link (expected https://<host>/m#tkc1...)")
    return f"{url.scheme.lower()}://{url.netloc.lower()}"


def check_base(base: str) -> str:
    """https only, except a local server over http. Anything else would send the token somewhere it should not go."""
    url = urllib.parse.urlsplit((base or "").strip())
    host = (url.hostname or "").lower()
    if not host:
        raise SystemExit(f"not a Turnkeeper address: {base!r}")
    if url.scheme != "https" and not (url.scheme == "http" and host in LOCAL_HOSTS):
        raise SystemExit(f"refusing {url.scheme or 'no'}:// for {host}: the token goes over https only")
    return base.strip().rstrip("/")


def resolve_base(raw: str, base: str | None) -> str:
    """The link's own host wins; an explicit --base that points elsewhere is refused, not silently used."""
    own = link_base(raw)
    if own is None:
        return check_base(base or os.environ.get("TURNKEEPER_BASE") or DEFAULT_BASE)
    if base and check_base(base).lower() != own:
        raise SystemExit(
            f"the link is for {own} but --base is {base}: refusing to send its token to another host"
        )
    return check_base(own)


def call(
    base: str, method: str, path: str, token: str, body: dict | None = None, timeout: float = 120.0
) -> tuple[int, dict]:
    base = check_base(base)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header(HEADER, token)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 - base passed check_base (https or local http)
            return resp.status, _json(resp.read())
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            where = e.headers.get("Location", "?") if e.headers else "?"
            return e.code, {"detail": f"redirected to {where}; refused so the token is not forwarded"}
        payload = _json(e.read())
        return e.code, payload if payload else {"detail": e.reason}


def _json(raw: bytes) -> dict:
    try:
        out = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return {"detail": raw.decode("utf-8", "replace")[:200]}
    return out if isinstance(out, dict) else {"body": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["request", "answer", "repayment"])
    ap.add_argument(
        "--base",
        default=None,
        help="Turnkeeper host (default: the link's own host, TURNKEEPER_BASE, or the demo)",
    )
    ap.add_argument("--cap", default=None, help="token or passbook link (prefer the TURNKEEPER_CAP env var)")
    ap.add_argument("--choice", choices=["yes", "no"])
    ap.add_argument("--rationale", default="")
    a = ap.parse_args(argv)
    raw = a.cap or os.environ.get("TURNKEEPER_CAP", "")
    token = token_from(raw)
    base = resolve_base(raw, a.base)
    if a.command == "request":
        status, out = call(base, "GET", "/api/member/request", token)
    elif a.command == "repayment":
        status, out = call(base, "GET", "/api/member/repayment", token)
    else:
        if not a.choice:
            raise SystemExit("answer needs --choice yes or --choice no (ask the member first)")
        status, out = call(
            base, "POST", "/api/member/answer", token, {"choice": a.choice, "rationale": a.rationale[:280]}
        )
    print(
        json.dumps(
            {"status": status, **out},
            ensure_ascii=False,
            indent=1,
        )
    )
    if status == 200:
        return 0
    detail = out.get("detail")
    return (
        2
        if isinstance(detail, dict)
        and str(detail.get("error", "")).startswith(("CAPABILITY_", "SET_", "NOT_A"))
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
