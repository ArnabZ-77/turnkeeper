"""Charter compiler: pasted circle rules (English / Hindi) -> a draft ``app.domain.charter.Charter``.

Two producers fill the same ``CharterDraft`` shape, and one deterministic ``assemble`` turns it into a validated
Charter plus per-field explanations and problems:

- the model (``app.agent.llm.complete_text``: one tool-free structured-JSON turn through the provider layer and
  the replay cache; free tiers have tiny daily quotas, so a recorded answer replays first), and
- ``parse_rules``, a rules parser good enough for the demo texts in ``app/fixtures/demo_rules_{en,hi}.txt``
  (both compile to exactly the demo circle's charter, same SHA-256).

When neither yields a usable draft (no model and the parser cannot find an amount and at least two members),
the result is the demo template charter, labelled "rules mode: template". The mode label never claims AI that
did not run.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.charter import (
    ALL_REMEDIES,
    HANDLE_PATTERN,
    Charter,
    FeePolicy,
    RemedyType,
    charter_sha256,
    collateral_violation,
)

log = logging.getLogger("turnkeeper.agent.charter")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "charter.md"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DEMO_RULES = {"en": FIXTURES / "demo_rules_en.txt", "hi": FIXTURES / "demo_rules_hi.txt"}

MAX_TEXT_LEN = 4000
MAX_MEMBERS = 20
CHARTER_FIELDS = (
    "name",
    "amount",
    "cadence",
    "start_date",
    "members",
    "rotation",
    "fee_policy",
    "buffer",
    "grace_days",
    "quorum",
    "remedy_menu",
    "languages",
)

Mode = Literal["live", "replay", "rules"]
Source = Literal["model", "parser", "template"]


# ----------------------------------------------------------------------------------------------------------
# Draft shape (what the model returns and the parser fills)
# ----------------------------------------------------------------------------------------------------------


def _money_text(v: Any) -> Any:
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        raise ValueError("not a money value")
    if isinstance(v, int | float | Decimal):
        return str(v)
    raise ValueError("not a money value")


class MemberDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=60)
    lang: str | None = Field(default=None, max_length=12)
    payday_rule: str | None = Field(default=None, max_length=80)
    track_record_rounds: int | None = Field(default=None, ge=0, le=240)


class CharterDraft(BaseModel):
    """Loose, all-optional draft. ``assemble`` fills defaults (as problems) and validates the Charter."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=80)
    amount: str | None = None  # "200.00"
    cadence: Literal["monthly", "weekly"] | None = None
    start_date: str | None = None  # YYYY-MM-DD
    members: list[MemberDraft] = Field(default_factory=list, max_length=MAX_MEMBERS)
    rotation: list[str] | None = None  # first names or handles, payout order
    fee_policy: FeePolicy | None = None
    buffer_seed: str | None = None
    grace_days: int | None = Field(default=None, ge=0, le=60)
    quorum: str | None = None  # "k/n"
    remedy_menu: list[RemedyType] | None = None
    languages: list[str] | None = None
    explanations: dict[str, str] = Field(default_factory=dict)

    @field_validator("amount", "buffer_seed", mode="before")
    @classmethod
    def _money(cls, v: Any) -> Any:
        return _money_text(v)

    @field_validator("explanations", mode="before")
    @classmethod
    def _clean_explanations(cls, v: Any) -> dict[str, str]:
        if not isinstance(v, dict):
            return {}
        return {str(k): str(t)[:240] for k, t in v.items() if str(k) in CHARTER_FIELDS and t}


def draft_json_schema() -> dict[str, Any]:
    """The schema shown to the model (deterministic, so the replay-cache key is stable)."""
    return CharterDraft.model_json_schema()


class CompileResult(BaseModel):
    ok: bool  # a valid Charter was produced
    mode: Mode  # who produced the draft: a live model, a recorded model answer, or deterministic rules
    source: Source  # model | parser | template
    mode_label: str  # honest UI label, e.g. "live AI (Gemini)", "AI replay", "rules mode: parser"
    lang: str
    charter: dict[str, Any] | None = None  # Charter.model_dump(mode="json")
    charter_sha256: str | None = None
    explanations: dict[str, str] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    draft: dict[str, Any] = Field(default_factory=dict)

    def charter_model(self) -> Charter | None:
        return Charter.model_validate(self.charter) if self.charter else None


# ----------------------------------------------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------------------------------------------

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUKTA = "़"


def fold(text: str) -> str:
    """Devanagari digits -> ASCII, nukta dropped (फ़ == फ), NFC. Latin text is unchanged (case kept)."""
    t = unicodedata.normalize("NFD", text.translate(_DEV_DIGITS)).replace(_NUKTA, "")
    return unicodedata.normalize("NFC", t)


def detect_lang(text: str) -> str:
    return "hi" if re.search(r"[ऀ-ॿ]", text) else "en"


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(fold(pattern), re.IGNORECASE)


_NUMBERING = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s*")


def _line_pairs(text: str) -> list[tuple[str, str]]:
    """(folded line, original line) with list numbering removed; blank lines dropped."""
    out = []
    for raw in text.splitlines():
        orig = _NUMBERING.sub("", raw.translate(_DEV_DIGITS)).strip()
        line = fold(orig)
        if line:
            out.append((line, orig))
    return out


def _lines(text: str) -> list[str]:
    return [line for line, _ in _line_pairs(text)]


def _strip_end(s: str) -> str:
    return s.strip().strip(".।|").strip()


def _quote(line: str) -> str:
    return line if len(line) <= 90 else line[:87] + "..."


def _labelled(lines: list[str], labels: str) -> tuple[str, str] | None:
    """The first line ``<label>...: rest`` -> (line, rest)."""
    rx = _rx(rf"^(?:{labels})[^:：]{{0,25}}[:：]\s*(.+)$")
    for line in lines:
        m = rx.match(line)
        if m:
            return line, m.group(1).strip()
    return None


def _has(line: str, words: str) -> bool:
    return bool(_rx(words).search(line))


_AMOUNT_RXS = (
    re.compile(r"(?:US)?\$\s*(\d[\d,]*(?:\.\d{1,2})?)", re.I),
    re.compile(r"(\d[\d,]*(?:\.\d{1,2})?)\s*(?:usd|dollars?|डॉलर)", re.I),
    re.compile(r"\busd\s*(\d[\d,]*(?:\.\d{1,2})?)", re.I),
)


def _amount_in(line: str) -> str | None:
    for rx in _AMOUNT_RXS:
        m = rx.search(line)
        if m:
            try:
                return format(Decimal(m.group(1).replace(",", "")).quantize(Decimal("0.01")), "f")
            except InvalidOperation:
                continue
    return None


_EN_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}  # fmt: skip
_HI_MONTHS = {
    fold(k): v
    for k, v in {
        "जनवरी": 1, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8,
        "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "अक्तूबर": 10, "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12,
        "दिसम्बर": 12,
    }.items()
}  # fmt: skip
_WORD = r"[A-Za-zऀ-ॿ]+"


def _month(word: str) -> int | None:
    w = word.lower().rstrip(".")
    return _EN_MONTHS.get(w) or _HI_MONTHS.get(fold(word))


def _date_in(line: str) -> date | None:
    candidates: list[tuple[int, int, int]] = []
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", line):
        candidates.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_WORD})\s*,?\s*(\d{{4}})", line):
        mon = _month(m.group(2))
        if mon:
            candidates.append((int(m.group(3)), mon, int(m.group(1))))
    for m in re.finditer(rf"({_WORD})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", line):
        mon = _month(m.group(1))
        if mon:
            candidates.append((int(m.group(3)), mon, int(m.group(2))))
    for m in re.finditer(rf"({_WORD})\s+(\d{{4}})", line):
        mon = _month(m.group(1))
        if mon:
            candidates.append((int(m.group(2)), mon, 1))
    for y, mo, d in candidates:
        try:
            return date(y, mo, d)
        except ValueError:
            continue
    return None


# Hindi payday phrases -> the English forms the inbox parser and the demo fixture use (longest first).
_HI_PAYDAYS = [
    (fold(k), v)
    for k, v in (
        ("हर दूसरे शुक्रवार", "every 2nd Friday"),
        ("महीने का आख़िरी कामकाजी दिन", "last working day"),
        ("आख़िरी कामकाजी दिन", "last working day"),
        ("महीने की 1 तारीख", "1st of the month"),
        ("15 और 30 तारीख", "15th and 30th"),
        ("हर शुक्रवार", "every Friday"),
        ("हर सोमवार", "every Monday"),
        ("हर गुरुवार", "every Thursday"),
    )
]

_LANGS = {
    "hindi": "hi", "english": "en", "spanish": "es", "español": "es", "gujarati": "gu", "punjabi": "pa",
    "tamil": "ta", "telugu": "te", "bengali": "bn", "urdu": "ur", "chinese": "zh", "mandarin": "zh",
    "tagalog": "tl", "vietnamese": "vi", "hi": "hi", "en": "en",
    fold("हिंदी"): "hi", fold("हिन्दी"): "hi", fold("अंग्रेज़ी"): "en", fold("गुजराती"): "gu",
}  # fmt: skip


def norm_lang(value: str | None) -> str | None:
    if not value:
        return None
    v = fold(value.strip()).lower()
    if re.fullmatch(r"[a-z]{2}", v):
        return v
    return _LANGS.get(v)


# ----------------------------------------------------------------------------------------------------------
# Rules parser (deterministic)
# ----------------------------------------------------------------------------------------------------------

_L_NAME = r"name|circle name|नाम|कमेटी का नाम"
_L_MEMBERS = r"members?|member list|people|सदस्य"
_L_ROTATION = (
    r"pot order|payout order|payout turns?|turn order|order of turns|rotation|पॉट का क्रम|बारी का क्रम|क्रम"
)
_L_TRACK = r"past circles|track records?|experience|पिछली"
_L_PAYDAY = r"paydays?|pay days|salary days?|salary|तनख़्वाह|तनख्वाह|वेतन|सैलरी"
_W_BUFFER = r"buffer|reserve|emergenc|cushion|बफ़र|आपात|रिज़र्व"
_W_GRACE = r"grace|late|छूट|मोहलत|देर"
_W_VOTE = r"vote|decision|quorum|approve|वोट|फ़ैसले|सहमति"
_W_SHORT = r"short|miss|can't pay|cannot pay|unable to pay|remed|कम पड़|नहीं भर|चूक"
_W_FEE = r"\bfees?\b|फ़ीस|शुल्क"
_W_START = r"start|begin|from|first round|शुरू|शुरुआत"
_REMEDY_WORDS: dict[str, str] = {
    "retry_on_date": r"retry|try again|charge again|on (?:their|his|her) payday|दोबारा|फिर से कोशिश",
    "turn_swap": r"swap|switch turns|trade turns|exchange turns|अदला|बारी बदल",
    "buffer_advance": r"advance|from the buffer|cover(?:ed)? by the buffer|बफ़र से|उधार",
    "repay_by_invoice": r"invoice|repay|bill|बिल|इनवॉइस|चुका",
}


def _split_list(rest: str) -> list[str]:
    parts = re.split(fold(r"\s*(?:,|;|،|\band\b|\s&\s|\sऔर\s)\s*"), _strip_end(rest), flags=re.IGNORECASE)
    out = []
    for p in parts:
        p = re.sub(r"\(.*?\)", "", p)
        p = _strip_end(p)
        if p:
            out.append(p)
    return out


def _first_sentence(rest: str) -> str:
    return re.split(r"(?<=[A-Za-zऀ-ॿ)])[.।]\s", rest + " ", maxsplit=1)[0]


class _Names:
    """Matches member references (first name, full name or handle) inside a line."""

    def __init__(self, members: list[MemberDraft]) -> None:
        self.keys: list[tuple[str, int]] = []
        for i, m in enumerate(members):
            full = m.name.strip().lower()
            first = full.split()[0] if full.split() else full
            for k in {full, first, handle_for(m.name, i)}:
                self.keys.append((k, i))
        self.keys.sort(key=lambda kv: len(kv[0]), reverse=True)

    def leading(self, chunk: str) -> tuple[int, str] | None:
        """(member index, remainder) when ``chunk`` starts with a member reference."""
        low = chunk.strip().lower()
        for k, i in self.keys:
            if low.startswith(k) and (len(low) == len(k) or not low[len(k)].isalpha()):
                return i, chunk.strip()[len(k) :]
        return None

    def found_in(self, line: str) -> list[int]:
        hits = []
        for k, i in self.keys:
            if re.search(rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])", line, re.IGNORECASE) and i not in hits:
                hits.append(i)
        return hits


def handle_for(name: str, index: int) -> str:
    """Logical handle from a display name: first name, lowercase ASCII ("Asha Patel" -> "asha")."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    first = re.sub(r"[^a-z0-9_]", "", ascii_name.split()[0] if ascii_name.split() else "")
    if re.fullmatch(HANDLE_PATTERN, first[:16]):
        return first[:16]
    return f"m{index + 1}"


def parse_rules(text: str) -> tuple[CharterDraft, dict[str, str]]:
    """Deterministic parse of pasted rules. Returns the draft and per-field evidence ("line N: ...")."""
    pairs = _line_pairs(text)
    lines = [line for line, _ in pairs]
    original = dict(pairs)
    ev: dict[str, str] = {}
    d: dict[str, Any] = {}

    def q(line: str) -> str:
        return _quote(original.get(line, line))

    def cite(field: str, line: str) -> None:
        ev[field] = f'Rules parser read: "{q(line)}"'

    # name
    hit = _labelled(lines, _L_NAME)
    if hit:
        d["name"] = _strip_end(hit[1])[:60]
        cite("name", hit[0])
    elif lines:
        head = re.split(r"\s+[-–—|]\s+|:\s", lines[0], maxsplit=1)[0].strip()
        if 2 <= len(head) <= 60 and not _amount_in(head):
            d["name"] = head
            cite("name", lines[0])

    # members
    members: list[MemberDraft] = []
    hit = _labelled(lines, _L_MEMBERS)
    if hit:
        for name in _split_list(hit[1])[:MAX_MEMBERS]:
            members.append(MemberDraft(name=name[:60]))
        cite("members", hit[0])
    names = _Names(members)

    # amount + cadence: the first amount on a line that is not about the buffer or fees
    for line in lines:
        if _has(line, _W_BUFFER):
            continue
        amt = _amount_in(line)
        if amt:
            d["amount"] = amt
            cite("amount", line)
            if _has(line, r"week|weekly|हफ़्ते|हफ्ते|साप्ताहिक"):
                d["cadence"] = "weekly"
                cite("cadence", line)
            elif _has(line, r"month|monthly|महीने|महीना|मासिक"):
                d["cadence"] = "monthly"
                cite("cadence", line)
            break

    # start date: lines that talk about starting first
    ordered = [ln for ln in lines if _has(ln, _W_START)] + lines
    for line in ordered:
        dt = _date_in(line)
        if dt:
            d["start_date"] = dt.isoformat()
            cite("start_date", line)
            break

    # rotation
    hit = _labelled(lines, _L_ROTATION)
    if hit:
        d["rotation"] = _split_list(_first_sentence(hit[1]))
        cite("rotation", hit[0])

    # track records
    hit = _labelled(lines, _L_TRACK)
    if hit and members:
        for chunk in _split_list(hit[1]):
            lead = names.leading(chunk)
            num = re.search(r"(\d+)", chunk)
            if lead and num:
                i = lead[0]
                members[i] = members[i].model_copy(update={"track_record_rounds": int(num.group(1))})
        ev["members"] = ev.get("members", "") + f' Track records: "{q(hit[0])}"'

    # paydays
    hit = _labelled(lines, _L_PAYDAY)
    if hit and members:
        for chunk in re.split(r"\s*[;؛]\s*", _strip_end(hit[1])):
            lead = names.leading(chunk)
            if not lead:
                continue
            i, rest = lead
            rule = _strip_end(rest.lstrip(" -–—:："))
            for hi, en in _HI_PAYDAYS:
                if hi in rule:
                    rule = en
                    break
            if rule:
                members[i] = members[i].model_copy(update={"payday_rule": rule[:80]})
        ev["members"] = ev.get("members", "") + f' Paydays: "{q(hit[0])}"'

    # member languages
    lang_rx = re.compile(
        "|".join(re.escape(k) for k in sorted(_LANGS, key=len, reverse=True) if len(k) > 2), re.I
    )
    for line in lines:
        if not _has(line, r"prefer|speak|message|language|संदेश|भाषा|में"):
            continue
        lm = lang_rx.search(line)
        if not lm:
            continue
        code = _LANGS.get(lm.group(0).lower()) or _LANGS.get(lm.group(0))
        for i in names.found_in(line):
            members[i] = members[i].model_copy(update={"lang": code})
            ev["languages"] = f'Rules parser read: "{q(line)}"'

    # buffer
    for line in lines:
        if _has(line, _W_BUFFER):
            amt = _amount_in(line)
            if amt:
                d["buffer_seed"] = amt
                cite("buffer", line)
                break

    # grace days
    for line in lines:
        if _has(line, _W_GRACE):
            m = re.search(fold(r"(\d+)\s*(?:days?|दिन)"), line, re.I)
            if m:
                d["grace_days"] = int(m.group(1))
                cite("grace_days", line)
                break

    # quorum
    for line in lines:
        if not _has(line, _W_VOTE):
            continue
        m = re.search(r"(\d+)\s*(?:of|out of|/)\s*(\d+)", line, re.I)
        if m:
            d["quorum"] = f"{int(m.group(1))}/{int(m.group(2))}"
        else:
            m = re.search(fold(r"(\d+)\s*में से\s*(\d+)"), line)
            if m:
                d["quorum"] = f"{int(m.group(2))}/{int(m.group(1))}"
        if "quorum" in d:
            cite("quorum", line)
            break

    # remedy menu
    for line in lines:
        if not _has(line, _W_SHORT):
            continue
        found = [t for t in ALL_REMEDIES if _has(line, _REMEDY_WORDS[t])]
        if found:
            d["remedy_menu"] = found
            cite("remedy_menu", line)
            break

    # fee policy
    for line in lines:
        if not _has(line, _W_FEE):
            continue
        if _has(line, r"organi[sz]er (?:pays|covers|absorbs)|आयोजक"):
            d["fee_policy"] = "organizer_absorbs"
        elif _has(line, r"on top|gross|members? (?:pay|cover)|extra|ऊपर से|अलग से"):
            d["fee_policy"] = "gross_up"
        elif _has(line, r"out of the pot|from the pot|net|deduct|पॉट से"):
            d["fee_policy"] = "net_pot"
        if "fee_policy" in d:
            cite("fee_policy", line)
            break

    d["members"] = members
    return CharterDraft(**d), ev


# ----------------------------------------------------------------------------------------------------------
# Assemble: draft -> validated Charter (shared by the model and the parser paths)
# ----------------------------------------------------------------------------------------------------------


def _next_month_start(today: date) -> date:
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


def assemble(
    draft: CharterDraft,
    *,
    evidence: dict[str, str] | None = None,
    input_lang: str = "en",
    today: date | None = None,
) -> tuple[Charter | None, dict[str, str], list[str]]:
    """Build the Charter. Missing fields get stated defaults (each one is a problem the organizer sees);
    the collateral rule is repaired by moving members with no track record to the end (also a problem)."""
    ex: dict[str, str] = dict(evidence or {})
    problems: list[str] = []
    today = today or date.today()

    def default(field: str, value: Any, why: str) -> Any:
        problems.append(f"{field}: {why}")
        ex[field] = f"Not stated; {why}."
        return value

    # members and handles
    members: list[dict[str, Any]] = []
    used: set[str] = set()
    for i, m in enumerate(draft.members[:MAX_MEMBERS]):
        h = handle_for(m.name, i)
        if h in used:
            parts = unicodedata.normalize("NFKD", m.name).encode("ascii", "ignore").decode().lower().split()
            alt = f"{h}_{parts[-1][0]}" if len(parts) > 1 and parts[-1][:1].isalpha() else f"{h}{i + 1}"
            h = alt[:16] if alt[:16] not in used else f"m{i + 1}"
        used.add(h)
        members.append(
            {
                "handle": h,
                "name": m.name.strip(),
                "lang": norm_lang(m.lang) or "en",
                "payday_rule": (m.payday_rule or None),
                "track_record_rounds": m.track_record_rounds or 0,
            }
        )
    if len(members) < 2:
        problems.append("members: a circle needs at least two members; none or one found in the text")
        return None, ex, problems

    n = len(members)
    names = _Names([MemberDraft(name=m["name"]) for m in members])

    # rotation
    rotation: list[str] = []
    unknown: list[str] = []
    for ref in draft.rotation or []:
        lead = names.leading(ref)
        if lead and not lead[1].strip():
            h = members[lead[0]]["handle"]
            if h not in rotation:
                rotation.append(h)
        else:
            unknown.append(ref)
    if unknown:
        problems.append(f"rotation: not a member: {', '.join(unknown)}")
    if not draft.rotation:
        tracked = [m["handle"] for m in members if m["track_record_rounds"] > 0]
        rotation = tracked + [m["handle"] for m in members if m["track_record_rounds"] == 0]
        default(
            "rotation", None, "payout order not stated; members with a track record go first, in listed order"
        )
    missing = [m["handle"] for m in members if m["handle"] not in rotation]
    if missing and draft.rotation:
        problems.append(f"rotation: {', '.join(missing)} missing from the stated order; added at the end")
    rotation += missing
    track = {m["handle"]: m["track_record_rounds"] for m in members}
    if collateral_violation(rotation, [_Mini(h, t) for h, t in track.items()]) is not None:
        fixed = [h for h in rotation if track[h] > 0] + [h for h in rotation if track[h] == 0]
        moved = [h for h in rotation if track[h] == 0 and rotation.index(h) != fixed.index(h)]
        problems.append(
            "rotation: collateral rule - members with no track record receive the pot last; moved "
            + ", ".join(moved)
            + " later"
        )
        ex["rotation"] = ex.get("rotation", "") + " Reordered for the collateral rule."
        rotation = fixed

    # amount
    amount = draft.amount
    try:
        amount_ok = amount is not None and Decimal(amount) > 0
    except InvalidOperation:
        amount_ok = False
    if not amount_ok:
        problems.append(f"amount: no positive contribution amount found ({amount!r})")
        return None, ex, problems

    cadence = draft.cadence or default("cadence", "monthly", "defaulted to monthly")
    try:
        start = date.fromisoformat(draft.start_date) if draft.start_date else None
    except ValueError:
        problems.append(f"start_date: {draft.start_date!r} is not a date")
        start = None
    if start is None:
        start = default("start_date", _next_month_start(today), "defaulted to the 1st of next month")

    # quorum
    k_default = max(1, math.ceil(2 * n / 3))
    quorum = draft.quorum
    if quorum and re.fullmatch(r"\d+/\d+", quorum):
        k, qn = (int(x) for x in quorum.split("/"))
        if qn != n or not 1 <= k <= n:
            k = min(max(k, 1), n)
            problems.append(f"quorum: {quorum} does not fit {n} members; using {k}/{n}")
            quorum = f"{k}/{n}"
    else:
        quorum = default("quorum", f"{k_default}/{n}", f"defaulted to two thirds ({k_default}/{n})")

    fee_policy = draft.fee_policy or default(
        "fee_policy", "net_pot", "defaulted to net_pot (PayPal fees come out of the pot)"
    )
    buffer_seed = draft.buffer_seed or default("buffer", "0.00", "no organizer buffer stated (0.00)")
    grace = (
        draft.grace_days if draft.grace_days is not None else default("grace_days", 3, "defaulted to 3 days")
    )
    if draft.remedy_menu:
        remedies = [t for t in ALL_REMEDIES if t in set(draft.remedy_menu)]
    else:
        remedies = default("remedy_menu", list(ALL_REMEDIES), "no remedies stated; all four are offered")

    langs = ["en"]
    for code in [*(norm_lang(x) for x in draft.languages or []), *(m["lang"] for m in members), input_lang]:
        if code and code not in langs:
            langs.append(code)
    if "languages" not in ex:
        ex["languages"] = "Members' message languages, English first."

    name = (draft.name or "").strip() or default("name", "My Circle", 'no circle name found; "My Circle"')

    try:
        charter = Charter.model_validate(
            {
                "name": name,
                "amount": amount,
                "cadence": cadence,
                "start_date": start,
                "members": members,
                "rotation": rotation,
                "fee_policy": fee_policy,
                "buffer": {"seed": buffer_seed},
                "grace_days": grace,
                "quorum": quorum,
                "remedy_menu": remedies,
                "languages": langs,
            }
        )
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", ())) or "charter"
            problems.append(f"{loc}: {err.get('msg', 'invalid')}")
        return None, ex, problems
    for f in CHARTER_FIELDS:
        ex.setdefault(f, "Taken from the rules text.")
    return charter, ex, problems


class _Mini:
    """Duck-typed member for ``collateral_violation`` before the Charter exists."""

    def __init__(self, handle: str, track: int) -> None:
        self.handle = handle
        self.track_record_rounds = track


# ----------------------------------------------------------------------------------------------------------
# Template fallback and the model path
# ----------------------------------------------------------------------------------------------------------


def template_charter() -> Charter:
    from app.engine.fixture import fixture_charter

    return fixture_charter()


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def escape_rules_text(text: str) -> str:
    """Pasted rules are data: angle brackets become look-alikes (U+2039/U+203A) so a paste cannot close or open
    the <rules_text> block and add instructions outside it (as eval/run_eval.py does for member replies)."""
    return text.strip().replace("<", "‹").replace(">", "›")


def user_prompt(text: str, lang: str) -> str:
    schema = json.dumps(draft_json_schema(), sort_keys=True, separators=(",", ":"))
    safe = escape_rules_text(text)
    return (
        f"Rules language hint: {lang}.\n"
        f"<rules_text>\n{safe}\n</rules_text>\n\n"
        f"Answer with one JSON object that validates against this JSON Schema:\n{schema}"
    )


def parse_model_json(text: str) -> CharterDraft:
    """The model's answer -> CharterDraft (tolerates code fences and prose around the object)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the model answer")
    data = json.loads(t[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the model answer is not a JSON object")
    return CharterDraft.model_validate(data)


async def _model_draft(
    text: str, lang: str, *, provider: Any, live_allowed: bool
) -> tuple[CharterDraft | None, str, str, str]:
    """(draft | None, mode, provider label, note). Never raises."""
    from app.agent.llm import complete_text, get_llm_provider
    from app.config import get_settings

    settings = get_settings()
    owns = provider is None
    if provider is None:
        try:
            provider = get_llm_provider(settings, live=live_allowed)
        except Exception as e:  # noqa: BLE001 - a broken provider config must not break compiling
            log.info("charter compiler: no provider (%s)", e)
            provider = None
    if provider is None:
        return None, "rules", "", "No AI provider configured or recorded; deterministic rules parser used."
    label = getattr(provider, "label", "AI")
    try:
        answer, mode = await complete_text(
            system_prompt(),
            user_prompt(text, lang),
            max_tokens=min(settings.llm_max_tokens, 2048),
            provider=provider,
            live_allowed=live_allowed,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("charter compiler model call failed: %s", type(e).__name__)
        answer, mode = None, "rules"
    finally:
        if owns:
            await provider.aclose()
    if answer is None:
        why = "no recorded answer for this text" if not getattr(provider, "live", False) else "AI unavailable"
        return None, "rules", label, f"{why} ({label}); deterministic rules parser used."
    try:
        return parse_model_json(answer), mode, label, ""
    except (ValueError, ValidationError) as e:
        log.info("charter compiler: model JSON rejected: %s", str(e)[:200])
        return None, "rules", label, f"The AI answer ({label}) was not valid charter JSON; rules parser used."


async def compile_charter(
    text: str,
    lang: str | None = None,
    *,
    provider: Any = None,
    live_allowed: bool = True,
    use_model: bool = True,
    today: date | None = None,
) -> CompileResult:
    """Pasted rules -> CompileResult. Order: model (cache first) -> rules parser -> template."""
    text = (text or "")[:MAX_TEXT_LEN]
    in_lang = norm_lang(lang) or detect_lang(text)
    notes: list[str] = []

    if use_model and text.strip():
        draft, mode, label, note = await _model_draft(
            text, in_lang, provider=provider, live_allowed=live_allowed
        )
        if note:
            notes.append(note)
        if draft is not None:
            charter, ex, problems = assemble(
                draft, evidence=draft.explanations, input_lang=in_lang, today=today
            )
            if charter is not None:
                mode_label = f"live AI ({label})" if mode == "live" else f"AI replay ({label}, recorded)"
                notes.append(
                    "Drafted by a model; the validator checked every field. Review before confirming."
                )
                return _result(True, mode, "model", mode_label, in_lang, charter, ex, problems, notes, draft)
            notes.append(f"The AI draft did not validate ({'; '.join(problems)[:200]}); rules parser used.")

    draft, evidence = parse_rules(text)
    charter, ex, problems = assemble(draft, evidence=evidence, input_lang=in_lang, today=today)
    if charter is not None:
        return _result(
            True, "rules", "parser", "rules mode: parser", in_lang, charter, ex, problems, notes, draft
        )

    tpl = template_charter()
    notes.append(
        "rules mode: template - the rules parser could not read "
        + "; ".join(problems)
        + f'. Showing the "{tpl.name}" template instead; edit the rules and compile again.'
    )
    ex = {f: "Template value (not read from your text)." for f in CHARTER_FIELDS}
    return _result(
        True, "rules", "template", "rules mode: template", in_lang, tpl, ex, problems, notes, draft
    )


def _result(
    ok: bool,
    mode: Mode,
    source: Source,
    label: str,
    lang: str,
    charter: Charter,
    ex: dict[str, str],
    problems: list[str],
    notes: list[str],
    draft: CharterDraft,
) -> CompileResult:
    return CompileResult(
        ok=ok,
        mode=mode,
        source=source,
        mode_label=label,
        lang=lang,
        charter=charter.model_dump(mode="json"),
        charter_sha256=charter_sha256(charter),
        explanations={f: ex.get(f, "") for f in CHARTER_FIELDS},
        problems=problems,
        notes=notes,
        draft=draft.model_dump(mode="json", exclude={"explanations"}),
    )


def demo_rules(lang: str = "en") -> str:
    return DEMO_RULES.get(lang, DEMO_RULES["en"]).read_text(encoding="utf-8")


def replay_available(text: str, lang: str | None = None) -> bool:
    """True when a recorded model answer for this exact text exists for the live identity, so compiling it
    spends no live-AI budget (same cache key as ``complete_text``). False whenever no key is configured."""
    try:
        from app.agent.llm import TurnCache, cache_key
        from app.config import get_settings

        settings = get_settings()
        if not settings.llm_configured:
            return False
        text = (text or "")[:MAX_TEXT_LEN]
        in_lang = norm_lang(lang) or detect_lang(text)
        key = cache_key(
            provider=settings.llm_provider,
            model=settings.llm_model,
            system=system_prompt(),
            tools=[],
            history=[{"role": "user", "text": user_prompt(text, in_lang)}],
        )
        hit = TurnCache(settings.llm_cache).get(key)
        return hit is not None and hit.stop == "end"
    except Exception:  # noqa: BLE001 - only an optimisation of the budget
        return False
