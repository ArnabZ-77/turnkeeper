#!/usr/bin/env python3
"""Turnkeeper member assistant: read the request, answer it once, check the repayment. Standard library only.

    TURNKEEPER_CAP='tkc1...' python3 member.py request   [--base URL]
    TURNKEEPER_CAP='tkc1...' python3 member.py answer --choice yes|no [--rationale TEXT] [--base URL]
    TURNKEEPER_CAP='tkc1...' python3 member.py repayment [--base URL]

The token goes in the X-Turnkeeper-Capability header (never the URL). A passbook link (https://host/m#tkc1...) is
accepted too: the part after '#' is used. Prints one JSON object; exit 0 on success, 2 on a named refusal, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://turnkeeper-s8xv.onrender.com"
HEADER = "X-Turnkeeper-Capability"


def token_from(raw: str) -> str:
    raw = (raw or "").strip()
    if "#" in raw:
        raw = raw.split("#", 1)[1]
    if not raw.startswith("tkc1."):
        raise SystemExit("not a Turnkeeper member token (it starts with tkc1.)")
    return raw


def call(
    base: str, method: str, path: str, token: str, body: dict | None = None, timeout: float = 120.0
) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    req.add_header(HEADER, token)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed scheme from --base
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            payload = {"detail": e.reason}
        return e.code, payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["request", "answer", "repayment"])
    ap.add_argument("--base", default=os.environ.get("TURNKEEPER_BASE", DEFAULT_BASE))
    ap.add_argument("--cap", default=None, help="token or passbook link (prefer the TURNKEEPER_CAP env var)")
    ap.add_argument("--choice", choices=["yes", "no"])
    ap.add_argument("--rationale", default="")
    a = ap.parse_args(argv)
    token = token_from(a.cap or os.environ.get("TURNKEEPER_CAP", ""))
    if a.command == "request":
        status, out = call(a.base, "GET", "/api/member/request", token)
    elif a.command == "repayment":
        status, out = call(a.base, "GET", "/api/member/repayment", token)
    else:
        if not a.choice:
            raise SystemExit("answer needs --choice yes or --choice no (ask the member first)")
        status, out = call(
            a.base, "POST", "/api/member/answer", token, {"choice": a.choice, "rationale": a.rationale[:280]}
        )
    print(
        json.dumps(
            {"status": status, **(out if isinstance(out, dict) else {"body": out})},
            ensure_ascii=False,
            indent=1,
        )
    )
    if status == 200:
        return 0
    detail = out.get("detail") if isinstance(out, dict) else None
    return (
        2
        if isinstance(detail, dict)
        and str(detail.get("error", "")).startswith(("CAPABILITY_", "SET_", "NOT_A"))
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
