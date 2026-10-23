"""Member inbox: replies in, treasurer messages out, and the rules-mode commitment parser.

Member text is DATA. It is stored verbatim, shown to the model only inside a ``<member_reply>`` block, and
never turns into money on its own: only ``record_commitment`` (schema-checked) does that, and the amount it
records is capped at one contribution. The rules parser below reads a payday from the text and ignores any
amount written in it (the commitment is always the member's full share).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

from sqlmodel import select

from app.db import session_scope
from app.engine.common import iso, utcnow
from app.models import InboxMessage

log = logging.getLogger("turnkeeper.engine.inbox")

TEXT_MAX_CHARS = 1000
# "Salary comes on Friday, I will pay then." (the demo reply; also in the fixture's history for ravi)
DEFAULT_RAVI_REPLY = "शुक्रवार को सैलरी आएगी, तब भर दूंगा"
DEFAULT_RAVI_REPLY_EN = "Salary comes on Friday, I will pay then."

# weekday index (Monday = 0) -> words that name it, English, Hindi (Devanagari) and common romanizations
_WEEKDAYS: dict[int, tuple[str, ...]] = {
    0: ("monday", "mon", "सोमवार", "somvar", "somwar"),
    1: ("tuesday", "tue", "tues", "मंगलवार", "mangalvar", "mangalwar"),
    2: ("wednesday", "wed", "बुधवार", "budhvar", "budhwar"),
    3: ("thursday", "thu", "thurs", "गुरुवार", "बृहस्पतिवार", "guruvar", "guruwar"),
    4: ("friday", "fri", "शुक्रवार", "shukravar", "shukrawar", "sukravar"),
    5: ("saturday", "sat", "शनिवार", "shanivar", "shaniwar"),
    6: ("sunday", "sun", "रविवार", "ravivar", "raviwar", "इतवार"),
}
_TOMORROW = ("tomorrow", "कल", "kal")
_ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")

_OUT_TEMPLATES = {
    "decline": {
        "en": "Hi {name}, your round {round_n} contribution of {amount} did not go through. When can you pay?",
        "hi": "नमस्ते {name}, राउंड {round_n} का आपका {amount} का भुगतान नहीं हो पाया। आप कब भर पाएंगे?",
    },
    "invoice": {
        "en": "Hi {name}, the circle covered your share. Here is your PayPal invoice for {amount}, due {due}.",
        "hi": "नमस्ते {name}, सर्कल ने आपका हिस्सा भर दिया है। {amount} का PayPal इनवॉइस, देय तिथि {due}।",
    },
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,.;:!?।()\"']+", text.lower()) if t]


def parse_payday(text: str, *, today: date) -> date | None:
    """The date a reply names: an ISO date, "tomorrow", or the next weekday it mentions (after ``today``)."""
    if not text:
        return None
    m = _ISO_DATE.search(text)
    if m:
        try:
            when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            when = None
        if when is not None and when >= today:
            return when
    tokens = _tokens(text)
    for weekday, words in _WEEKDAYS.items():
        if any(w in tokens for w in words) or any(w in text for w in words if not w.isascii()):
            days = (weekday - today.weekday()) % 7 or 7
            return today + timedelta(days=days)
    if any(w in tokens for w in _TOMORROW):
        return today + timedelta(days=1)
    return None


def outbound_text(kind: str, lang: str, **fields: Any) -> str:
    templates = _OUT_TEMPLATES[kind]
    return templates.get(lang, templates["en"]).format(**fields)


def _row_dict(row: InboxMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "member": row.member,
        "lang": row.lang,
        "text": row.text,
        "direction": row.direction,
        "source": row.source,
        "round_n": row.round_n,
        "ts": iso(row.ts),
    }


def add_message(
    circle_id: str,
    member: str,
    text: str,
    *,
    lang: str = "en",
    direction: str = "in",
    source: str = "member",
    round_n: int | None = None,
) -> dict[str, Any]:
    """Store one message and return it as a dict."""
    if direction not in ("in", "out"):
        raise ValueError("direction must be 'in' or 'out'")
    body = (text or "").strip()[:TEXT_MAX_CHARS]
    if not body:
        raise ValueError("message text is empty")
    row = InboxMessage(
        circle_id=circle_id,
        member=member,
        lang=lang,
        text=body,
        direction=direction,
        source=source,
        round_n=round_n,
        ts=utcnow(),
        json=json.dumps({"member": member, "lang": lang, "direction": direction, "source": source}),
    )
    with session_scope() as db:
        db.add(row)
        db.flush()
        out = _row_dict(row)
    log.info("inbox %s %s (%s, %d chars)", direction, member, lang, len(body))
    return out


def list_messages(
    circle_id: str, *, member: str | None = None, direction: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Messages oldest first (the last ``limit``)."""
    with session_scope() as db:
        q = select(InboxMessage).where(InboxMessage.circle_id == circle_id)
        if member:
            q = q.where(InboxMessage.member == member)
        if direction:
            q = q.where(InboxMessage.direction == direction)
        rows = list(db.exec(q.order_by(InboxMessage.id.desc()).limit(limit)).all())  # type: ignore[union-attr]
    return [_row_dict(r) for r in reversed(rows)]


def latest_reply(circle_id: str, member: str) -> dict[str, Any] | None:
    replies = list_messages(circle_id, member=member, direction="in", limit=1)
    return replies[-1] if replies else None


def as_member_reply_block(msg: dict[str, Any]) -> str:
    """How a reply is shown to the model: a data block, never an instruction."""
    text = str(msg.get("text") or "").replace("<", "‹").replace(">", "›")
    return f'<member_reply member="{msg.get("member")}" lang="{msg.get("lang")}">{text}</member_reply>'
