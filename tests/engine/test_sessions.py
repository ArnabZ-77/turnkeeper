"""Session isolation: one cookie -> one cloned circle with its own run_id."""

import pytest

from app.config import get_settings
from app.engine import sessions, state
from app.engine.sessions import WaitlistFull, create_session, reset_session, resolve_session


def test_two_sessions_get_two_circles_with_different_run_ids():
    a = create_session(enforce_cap=False)
    b = create_session(enforce_cap=False)
    assert a.sid != b.sid
    assert a.circle_id != b.circle_id
    assert a.run_id != b.run_id
    assert len(a.run_id) == 8 and a.run_id.islower()
    assert state.load_circle(a.circle_id).run_id == a.run_id
    assert state.load_circle(b.circle_id).run_id == b.run_id
    assert sessions.sids_for_circle(a.circle_id) == [a.sid]


def test_reset_session_gives_a_new_clone_and_closes_the_old_one():
    sess = create_session(enforce_cap=False)
    old_circle, old_run = sess.circle_id, sess.run_id
    new = reset_session(sess.sid)
    assert new.sid == sess.sid
    assert new.circle_id != old_circle and new.run_id != old_run
    assert state.load_circle(old_circle).status == "closed"
    assert state.load_circle(new.circle_id).status == "active"
    assert resolve_session(sess.sid).circle_id == new.circle_id


def test_reset_unknown_session_raises():
    with pytest.raises(LookupError):
        reset_session("nope")


def test_resolve_session_handles_missing_and_empty():
    assert resolve_session(None) is None
    assert resolve_session("") is None
    assert resolve_session("unknown-sid") is None


def test_active_cap_raises_waitlist(monkeypatch):
    monkeypatch.setenv("TK_MAX_ACTIVE_SESSIONS", "1")
    get_settings.cache_clear()
    create_session()
    with pytest.raises(WaitlistFull) as exc:
        create_session()
    assert exc.value.active == 1 and exc.value.maximum == 1
    assert sessions.active_count() == 1


def test_circle_publisher_reaches_every_owner_session():
    import asyncio

    from app.engine.events import get_event_bus

    sess = create_session(enforce_cap=False)
    publish = sessions.circle_publisher(sess.circle_id)
    asyncio.run(publish({"type": "toast", "level": "info", "text": "hi"}))
    recent = get_event_bus().recent(sess.sid)
    assert recent and recent[-1]["type"] == "toast" and "ts" in recent[-1]
