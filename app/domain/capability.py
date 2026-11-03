"""Member capabilities: short-lived HMAC tokens that let a member's own AI assistant act for one member only.

A capability is minted by the server (``mint``) and checked by ``verify``. It binds:

- ``circle_id``, ``member``: whose capability it is (another member's request is ``CAPABILITY_SCOPE``);
- ``set_id`` and ``set_sha``: the exact remedy set it is about (a newer or changed set is ``SET_SUPERSEDED``);
- ``scope``: ``read`` (see the request, the passbook, the repayment) or ``answer`` (also answer it once);
- ``item``: what an answer is for (``consent`` or ``vote``) and ``choices`` (the answers allowed);
- ``exp``: expiry (``CAPABILITY_EXPIRED``), at most ``MAX_TTL`` after minting;
- ``nonce``: one answer per token, enforced by a database row (``app.engine.capabilities``), not in memory.

Pure: no I/O, no clock (``now`` is passed in). The signature is HMAC-SHA256 over the canonical claims.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

TOKEN_PREFIX = "tkc1"
DEFAULT_TTL = 15 * 60
MAX_TTL = 24 * 3600  # a read-only passbook link may live a day; an answer token lives 15 minutes
ANSWER_TTL = DEFAULT_TTL

Scope = Literal["read", "answer"]
Item = Literal["consent", "vote"]

# Named refusals (the MCP tools and routes return these codes).
CAPABILITY_INVALID = "CAPABILITY_INVALID"
CAPABILITY_EXPIRED = "CAPABILITY_EXPIRED"
CAPABILITY_SCOPE = "CAPABILITY_SCOPE"
CAPABILITY_USED = "CAPABILITY_USED"
SET_SUPERSEDED = "SET_SUPERSEDED"
NOT_A_DEMO_CIRCLE = "NOT_A_DEMO_CIRCLE"


class CapabilityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, object]:
        return {"ok": False, "error": self.code, "message": self.message}


class Capability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    circle_id: str = Field(min_length=1, max_length=40)
    member: str = Field(min_length=1, max_length=40)
    scope: Scope
    set_id: str | None = Field(default=None, max_length=40)
    set_sha: str | None = Field(default=None, max_length=64)
    item: Item | None = None
    choices: tuple[str, ...] = ()
    iat: int
    exp: int
    nonce: str = Field(min_length=8, max_length=40)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _payload(cap: Capability) -> bytes:
    return json.dumps(cap.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(payload: bytes, secret: str) -> str:
    key = hashlib.sha256(("capability:" + secret).encode("utf-8")).digest()  # never the mandate key itself
    return _b64(hmac.new(key, payload, hashlib.sha256).digest())


def set_digest(remedy_set_json: str) -> str:
    """sha256 of a remedy set's stored JSON: any edit to the set invalidates its capabilities."""
    return hashlib.sha256(remedy_set_json.encode("utf-8")).hexdigest()


def mint(
    *,
    circle_id: str,
    member: str,
    scope: Scope,
    now: int,
    secret: str,
    ttl: int = DEFAULT_TTL,
    set_id: str | None = None,
    set_sha: str | None = None,
    item: Item | None = None,
    choices: tuple[str, ...] = ("yes", "no"),
) -> tuple[str, Capability]:
    if not secret:
        raise ValueError("a signing secret is required")
    if scope == "answer" and (item is None or not set_id or not set_sha):
        raise ValueError("an answer capability needs item, set_id and set_sha")
    ttl = max(1, min(int(ttl), MAX_TTL if scope == "read" else ANSWER_TTL))
    cap = Capability(
        circle_id=circle_id,
        member=member,
        scope=scope,
        set_id=set_id,
        set_sha=set_sha,
        item=item if scope == "answer" else None,
        choices=tuple(choices) if scope == "answer" else (),
        iat=int(now),
        exp=int(now) + ttl,
        nonce=secrets.token_urlsafe(12),
    )
    payload = _payload(cap)
    return f"{TOKEN_PREFIX}.{_b64(payload)}.{_sign(payload, secret)}", cap


def verify(token: str, *, secret: str, now: int) -> Capability:
    """The token's claims, or ``CapabilityError`` (invalid or expired). Scope is checked by the caller."""
    if not isinstance(token, str) or len(token) > 2048:
        raise CapabilityError(CAPABILITY_INVALID, "not a Turnkeeper capability")
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise CapabilityError(CAPABILITY_INVALID, "not a Turnkeeper capability")
    try:
        payload = _unb64(parts[1])
    except (ValueError, TypeError):
        raise CapabilityError(CAPABILITY_INVALID, "malformed capability") from None
    if not secret or not hmac.compare_digest(_sign(payload, secret), parts[2]):
        raise CapabilityError(CAPABILITY_INVALID, "capability signature does not match")
    try:
        cap = Capability.model_validate(json.loads(payload))
    except (ValueError, ValidationError):
        raise CapabilityError(CAPABILITY_INVALID, "malformed capability") from None
    if now >= cap.exp:
        raise CapabilityError(
            CAPABILITY_EXPIRED, "this capability has expired; ask the organizer for a new link"
        )
    return cap


def require_member(cap: Capability, member: str | None) -> None:
    """A capability acts for its own member only."""
    if member and member != cap.member:
        raise CapabilityError(
            CAPABILITY_SCOPE, f"this token answers one question, for {cap.member} only (asked for {member})"
        )


def require_answer(cap: Capability, choice: str) -> None:
    if cap.scope != "answer":
        raise CapabilityError(CAPABILITY_SCOPE, "this token is read-only; it cannot answer")
    if choice not in cap.choices:
        raise CapabilityError(
            CAPABILITY_SCOPE, f"answer {choice!r} is not allowed; allowed: {', '.join(cap.choices)}"
        )
