"""Browser sessions: cookie tk_sid -> one cloned demo circle with its own run_id.

Cap: settings.max_active_sessions sessions seen within the last 20 minutes; beyond that a new
visitor gets HTTP 429 {"waitlist": true} (raised here as WaitlistFull, rendered by the route).
"""

from __future__ import annotations

import importlib
import logging
import secrets
import string
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Response
from sqlmodel import Session as DbSession
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.engine.common import utcnow
from app.engine.events import Publish, get_event_bus
from app.engine.fixture import clone_fixture
from app.mcp.auth import RateLimiter, client_ip
from app.models import Circle, Session

log = logging.getLogger("turnkeeper.engine.sessions")

COOKIE_NAME = "tk_sid"
COOKIE_MAX_AGE = 7 * 24 * 3600
IDLE_TTL = timedelta(minutes=20)
_ALPHABET = string.ascii_lowercase + string.digits
_BASE32_LOWER = "abcdefghijklmnopqrstuvwxyz234567"


_SESSION_WINDOW_S = 3600.0
_session_limiter: RateLimiter | None = None


def session_limiter() -> RateLimiter:
    """Per-IP budget for opening NEW sessions (one cookie-less GET makes one clone); lazily built."""
    global _session_limiter
    if _session_limiter is None:
        _session_limiter = RateLimiter(limit=1, window=_SESSION_WINDOW_S)
    _session_limiter.limit = max(1, get_settings().session_creates_per_ip_hour)
    return _session_limiter


class WaitlistFull(Exception):
    """Raised when the active-session cap is reached. Routes answer HTTP 429 {"waitlist": true}."""

    def __init__(self, active: int, maximum: int) -> None:
        super().__init__(f"{active} active sessions (max {maximum})")
        self.active = active
        self.maximum = maximum


def new_run_id() -> str:
    """8-char lowercase base32 from app.paypal.keys; a local fallback keeps sessions working without B1."""
    try:
        return importlib.import_module("app.paypal.keys").new_run_id()
    except (ImportError, AttributeError):
        log.warning("app.paypal.keys unavailable; using local run_id generator")
        return "".join(secrets.choice(_BASE32_LOWER) for _ in range(8))


def new_sid() -> str:
    return secrets.token_urlsafe(24)


def _new_circle_id(db: DbSession) -> str:
    while True:
        cid = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        if db.get(Circle, cid) is None:
            return cid


def _active_count(db: DbSession, now: datetime) -> int:
    cutoff = now - IDLE_TTL
    return len(db.exec(select(Session.sid).where(Session.last_seen >= cutoff)).all())


def active_count(now: datetime | None = None) -> int:
    with session_scope() as db:
        return _active_count(db, now or utcnow())


def _clone_for(db: DbSession, run_id: str, now: datetime) -> Circle:
    circle_id = _new_circle_id(db)
    return clone_fixture(db, circle_id=circle_id, run_id=run_id, now=now)


def create_session(sid: str | None = None, *, enforce_cap: bool = True) -> Session:
    """Create a session with a fresh fixture clone (used by get_or_create_session and tests)."""
    settings = get_settings()
    now = utcnow()
    with session_scope() as db:
        if enforce_cap:
            active = _active_count(db, now)
            if active >= settings.max_active_sessions:
                raise WaitlistFull(active, settings.max_active_sessions)
        run_id = new_run_id()
        circle = _clone_for(db, run_id, now)
        sess = Session(
            sid=sid or new_sid(), run_id=run_id, circle_id=circle.id, created_at=now, last_seen=now
        )
        db.add(sess)
        db.commit()
        log.info("session created sid=%s... circle=%s run=%s", sess.sid[:6], circle.id, run_id)
        return sess


def resolve_session(sid: str | None) -> Session | None:
    if not sid:
        return None
    with session_scope() as db:
        sess = db.get(Session, sid)
        if sess is None:
            return None
        if db.get(Circle, sess.circle_id) is None:  # database wiped under a stale cookie
            return None
        return sess


def touch(sid: str) -> None:
    with session_scope() as db:
        sess = db.get(Session, sid)
        if sess is not None:
            sess.last_seen = utcnow()
            db.add(sess)


def request_is_https(request: Request | None) -> bool:
    """https as seen by the browser: the request scheme or the proxy's X-Forwarded-Proto (Render terminates TLS)."""
    if request is None:
        return False
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def set_cookie(response: Response, sid: str, request: Request | None = None) -> None:
    # Secure whenever the browser is on https, even if PUBLIC_BASE_URL was left at its localhost default.
    secure = get_settings().public_base_url.startswith("https://") or request_is_https(request)
    response.set_cookie(
        COOKIE_NAME,
        sid,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def get_or_create_session(request: Request, response: Response) -> Session:
    """Session for the tk_sid cookie, creating (and cloning the fixture) when absent. Raises WaitlistFull."""
    settings = get_settings()
    now = utcnow()
    sid = request.cookies.get(COOKIE_NAME)
    existing = resolve_session(sid)
    if existing is not None:
        stale = existing.last_seen < now - IDLE_TTL
        if stale and active_count(now) >= settings.max_active_sessions:
            raise WaitlistFull(active_count(now), settings.max_active_sessions)
        touch(existing.sid)
        existing.last_seen = now
        set_cookie(response, existing.sid, request)
        return existing
    # A new visitor costs a database clone and one of the live slots: budget it per IP so a script cannot open
    # slots until real visitors are locked out. Only successful creations count against the budget.
    limiter = session_limiter()
    ip = client_ip(request.scope)
    blocked, _retry = limiter.is_limited(f"new-session|{ip}")
    if blocked:
        raise WaitlistFull(active_count(now), settings.max_active_sessions)
    sess = create_session()
    limiter.check(f"new-session|{ip}")
    set_cookie(response, sess.sid, request)
    return sess


def reset_session(sid: str) -> Session:
    """New run_id and a brand-new clone for the same cookie; the old circle is closed, not deleted."""
    now = utcnow()
    with session_scope() as db:
        sess = db.get(Session, sid)
        if sess is None:
            raise LookupError(f"session {sid[:6]}... not found")
        old = db.get(Circle, sess.circle_id)
        if old is not None:
            old.status = "closed"
            db.add(old)
        run_id = new_run_id()
        circle = _clone_for(db, run_id, now)
        sess.run_id = run_id
        sess.circle_id = circle.id
        sess.last_seen = now
        db.add(sess)
        db.commit()
        log.info("session reset sid=%s... circle=%s run=%s", sid[:6], circle.id, run_id)
        return sess


def sids_for_circle(circle_id: str) -> list[str]:
    with session_scope() as db:
        return list(db.exec(select(Session.sid).where(Session.circle_id == circle_id)).all())


def circle_publisher(circle_id: str) -> Publish:
    """Publish to every session that currently owns the circle (webhooks have no cookie)."""
    bus = get_event_bus()

    async def _publish(event: dict) -> None:
        for sid in sids_for_circle(circle_id):
            await bus.publish(sid, event)

    return _publish


async def require_session(request: Request) -> Session:
    """FastAPI dependency: the caller's existing session, else 401 (GET /api/session creates one)."""
    sess = resolve_session(request.cookies.get(COOKIE_NAME))
    if sess is None:
        raise HTTPException(status_code=401, detail="no session; call GET /api/session first")
    touch(sess.sid)
    return sess


def owns_circle(sess: Session, circle_id: str) -> None:
    if sess.circle_id != circle_id:
        raise HTTPException(status_code=403, detail="circle does not belong to this session")
