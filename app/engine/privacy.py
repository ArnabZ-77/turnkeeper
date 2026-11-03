"""Mask contact details in text before it leaves the app for a third-party model.

Visitors can type free text (charter rules, a reply as a member). The hosted demo may use a free-tier model
provider that can retain prompts, so emails and phone numbers are replaced by placeholders first. Names cannot
be detected reliably: the privacy notice tells visitors to use made-up names.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# +91 98765 43210, 98765-43210, (555) 123-4567, 0112345678: 8+ digits with optional separators.
_PHONE = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().-]{6,}\d(?![\w])")
_DIGITS = re.compile(r"\d")
_DATE = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")
# 32+ char opaque tokens (API keys, vault ids pasted by mistake).
_TOKEN = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b")


def _phone_sub(m: re.Match[str]) -> str:
    # amounts and dates ("1,000", "2026-11-01") contain few digits or other separators; require 8+ digits.
    raw = m.group(0)
    if _DATE.fullmatch(raw.strip()) or "," in raw or len(_DIGITS.findall(raw)) < 8:
        return raw
    return "<phone>"


def mask_pii(text: str) -> str:
    """Replace emails, phone numbers and long opaque tokens with placeholders."""
    if not text:
        return text
    out = _EMAIL.sub("<email>", text)
    out = _PHONE.sub(_phone_sub, out)
    return _TOKEN.sub("<token>", out)
