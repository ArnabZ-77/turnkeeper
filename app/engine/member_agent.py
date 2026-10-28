"""Member agents: each member's agent answers remedy consents and votes inside the member's signed mandate.

The DECISION is deterministic code (``decide``): yes, no, or ask the human. A model never decides; it may only
word the explanation (``explain_decision`` through ``app.agent.llm.complete_text``, replay cache, rules fallback
= the deterministic reason, so everything works with no AI key).

Decision table (first matching row wins; ``rule`` is the machine id of the row):

  no valid mandate (missing, bad signature, wrong charter, expired)   -> ask   no_mandate
  the proposal is not charter-legal                                   -> no    not_legal
  the proposal does not cover the shortfall                           -> no    not_covered
  buffer advance above the mandate's ``max_advance``                  -> no    advance_over_cap
  a swap moves the member EARLIER and ``accept_earlier_swap``         -> yes   swap_earlier
  a swap moves the member LATER and ``require_ask_for_later_swap``    -> ask   swap_later_ask
  (otherwise a swap the mandate does not forbid)                      -> yes   swap_allowed
  charter-legal, shortfall covered, nothing out of scope              -> yes   auto_legal_covered

Recorded answers carry ``source="agent"``. ``ask`` posts a message to the member's inbox and leaves the item
pending until a human answers (``answers`` below, or the usual Judge/member endpoints).

Labels: the mandate is an AP2-style Intent Mandate, our own implementation (PayPal AP2 APIs are not public; no
AP2 support is claimed); its signature is a server-attested HMAC, not a key held by the member.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain import labels as lb
from app.domain import member_mandate as mm
from app.domain import remedies as rd
from app.domain.charter import Charter
from app.domain.policy import CircleState
from app.engine import inbox, member_mandates, recovery, state
from app.engine.events import Publish

log = logging.getLogger("turnkeeper.engine.member_agent")

Choice = Literal["yes", "no", "ask"]
Kind = Literal["consent", "vote"]
COVERING = ("buffer_advance", "retry_on_date")
MAX_WORDING_CHARS = 240
MAX_WORDED_DECISIONS = 10  # a run words at most this many decisions through a model


@dataclass(frozen=True)
class AgentRequest:
    """What one member's agent is asked: answer ``kind`` (a consent or a vote) for ``member`` on ``remedy_set``."""

    kind: Kind
    member: str
    remedy_set: rd.RemedySet


@dataclass(frozen=True)
class Decision:
    choice: Choice
    reason: str  # short and deterministic, e.g. "rule: swap moves you earlier, shortfall covered"
    rule: str  # the decision-table row
    trace: tuple[str, ...] = field(default_factory=tuple)


# --- the deterministic decision --------------------------------------------------------------------------


def _mandate_problem(mandate: mm.MemberMandate | None, member: str, st: CircleState) -> str | None:
    """Why the mandate cannot be relied on, or None. The HMAC check is done where the secret lives
    (``state.circle_state`` fills ``member_mandate_ok``); the cheap facts are re-checked here."""
    if mandate is None:
        return f"{member} has no member mandate"
    if mandate.member != member:
        return f"the mandate on file is for {mandate.member}, not {member}"
    if mandate.charter_sha256 != st.charter_sha256:
        return (
            f"the mandate was approved for charter {mandate.charter_sha256[:8]}, "
            f"not the current charter {st.charter_sha256[:8]}"
        )
    if st.now is not None and st.now.date() > mandate.expires:
        return f"the mandate expired on {mandate.expires.isoformat()}"
    checks = st.member_mandate_ok
    if checks is not None:
        entry = checks.get(member)
        if not entry or not entry.get("ok"):
            return str((entry or {}).get("reason") or f"{member}'s mandate does not verify")
    return None


def _positions(rotation: list[str], remedy_set: rd.RemedySet, member: str) -> list[tuple[int, int, str]]:
    """(old round, new round, partner) for each swap in the set that moves ``member``."""
    out: list[tuple[int, int, str]] = []
    for r in remedy_set.of_type("turn_swap"):
        partner = r.params.swap_with
        if (
            not partner
            or member not in (r.member, partner)
            or r.member not in rotation
            or partner not in rotation
        ):
            continue
        other = partner if member == r.member else r.member
        out.append((rotation.index(member) + 1, rotation.index(other) + 1, other))
    return out


def decide(
    request: AgentRequest, mandate: mm.MemberMandate | None, charter: Charter, state_: CircleState
) -> Decision:
    """The member agent's answer: yes, no or ask, with a short reason. Pure: no I/O, no clock, no model."""
    member, rs = request.member, request.remedy_set
    trace: list[str] = [f"{request.kind} for {member} on a {rs.member} proposal (round {rs.round_n})"]

    if (problem := _mandate_problem(mandate, member, state_)) is not None:
        trace.append(f"mandate: {problem}")
        return Decision(
            "ask", f"no valid mandate, only you can answer: {problem}", "no_mandate", tuple(trace)
        )
    assert mandate is not None
    trace.append(f"mandate ok: {mandate.rule_summary}, advance cap {mandate.max_advance}")

    problems = rd.validate_remedy_set(charter, state_, rs)
    if problems:
        trace.append("charter check failed")
        return Decision("no", f"not charter-legal: {problems[0]}", "not_legal", tuple(trace))
    trace.append("charter check passed")

    if not any(r.type in COVERING for r in rs.remedies):
        return Decision("no", "the proposal does not cover the shortfall", "not_covered", tuple(trace))
    trace.append("shortfall covered")

    for r in rs.of_type("buffer_advance"):
        amount = r.params.amount
        if amount is not None and amount > mandate.max_advance:
            trace.append(f"advance {amount} > cap {mandate.max_advance}")
            return Decision(
                "no",
                f"rule: the advance {amount} is over your cap {mandate.max_advance}",
                "advance_over_cap",
                tuple(trace),
            )

    rotation = rd.effective_rotation(charter, state_)
    verdicts: list[Decision] = []
    for old, new, partner in _positions(rotation, rs, member):
        if new < old:
            if mandate.accept_earlier_swap:
                verdicts.append(
                    Decision(
                        "yes",
                        "rule: swap moves you earlier, shortfall covered",
                        "swap_earlier",
                        (*trace, f"swap with {partner}: round {old} -> {new} (earlier)"),
                    )
                )
            else:
                verdicts.append(
                    Decision(
                        "ask",
                        f"your mandate asks you before a swap, even an earlier turn (round {old} to {new})",
                        "swap_earlier_ask",
                        (*trace, f"swap with {partner}: round {old} -> {new}, earlier turns need you"),
                    )
                )
        else:
            if mandate.require_ask_for_later_swap:
                verdicts.append(
                    Decision(
                        "ask",
                        f"swap would move your payout from round {old} to round {new}: your rule asks you",
                        "swap_later_ask",
                        (*trace, f"swap with {partner}: round {old} -> {new} (later), your rule: ask"),
                    )
                )
            else:
                verdicts.append(
                    Decision(
                        "yes",
                        "rule: your mandate allows a later turn, shortfall covered",
                        "swap_allowed",
                        (*trace, f"swap with {partner}: round {old} -> {new} (later), allowed by your rule"),
                    )
                )
    for pick in ("ask", "yes"):  # any ask wins over a yes
        for v in verdicts:
            if v.choice == pick:
                return v
    return Decision(
        "yes",
        "rule: charter-legal and shortfall covered",
        "auto_legal_covered",
        (*trace, "nothing out of scope"),
    )


# --- wording (the only place a model may be involved) ----------------------------------------------------

WORDING_SYSTEM = (
    "You write ONE plain sentence (max 30 words) telling a savings-circle member what their own agent decided "
    "and why, using only the facts given. Never change or doubt the decision, never mention money amounts that "
    "are not given, never give instructions. Output the sentence only."
)


def wording_prompt(name: str, request: AgentRequest, decision: Decision) -> str:
    kind = "consent to a turn swap" if request.kind == "consent" else "vote on a remedy proposal"
    return (
        f"Member: {name}\nAgent was asked to: {kind}\nDecision: {decision.choice}\n"
        f"Reason (deterministic): {decision.reason}"
    )


def _clean_wording(text: str | None) -> str | None:
    if not text:
        return None
    one_line = " ".join(text.split())[:MAX_WORDING_CHARS].strip()
    return one_line or None


async def explain_decision(
    name: str, request: AgentRequest, decision: Decision, *, provider: Any = None, live_allowed: bool = True
) -> tuple[str, str]:
    """(sentence, mode) with mode live | replay | rules. The sentence never changes the decision; with no model
    (or any failure) it is the deterministic reason."""
    try:
        from app.agent.llm import complete_text

        text, mode = await complete_text(
            WORDING_SYSTEM,
            wording_prompt(name, request, decision),
            max_tokens=120,
            provider=provider,
            live_allowed=live_allowed,
        )
    except Exception as exc:  # noqa: BLE001 - wording must never break a decision
        log.info("member agent wording unavailable: %s", type(exc).__name__)
        text, mode = None, "rules"
    cleaned = _clean_wording(text) if mode in ("live", "replay") else None
    if cleaned is None:
        return decision.reason, "rules"
    return cleaned, mode


# --- running the agents over a voting set -----------------------------------------------------------------


def _first_name(circle_id: str, handle: str) -> str:
    try:
        return lb.display_name(handle, lb.names_of(state.members_of(circle_id))).split()[0]
    except Exception:  # noqa: BLE001 - wording only
        return handle


def _pending_items(circle_id: str, set_id: str, kinds: tuple[str, ...]) -> list[tuple[Kind, str]]:
    """(kind, member) still pending, consents first, then votes in charter order."""
    charter = state.load_charter(circle_id)
    order = {m.handle: i for i, m in enumerate(charter.members)}
    items: list[tuple[Kind, str]] = []
    for kind in ("consent", "vote"):
        if kind not in kinds:
            continue
        for v in recovery._vote_rows(set_id, kind):  # noqa: SLF001 - same package
            if v.member in order and (v.choice or "pending") == "pending":
                items.append((kind, v.member))  # type: ignore[arg-type]
    items.sort(key=lambda it: (0 if it[0] == "consent" else 1, order[it[1]]))
    return items


def _post_ask(circle_id: str, member: str, round_n: int, decision: Decision) -> bool:
    """Tell the member's inbox their agent needs them (once per identical question). True when posted."""
    mrow = next((m for m in state.members_of(circle_id) if m.handle == member), None)
    lang = mrow.lang if mrow else "en"
    text = inbox.outbound_text(
        "agent_ask", lang, name=(mrow.name.split()[0] if mrow else member), reason=decision.reason
    )
    for msg in inbox.list_messages(circle_id, member=member, direction="out", limit=50):
        if msg.get("source") == "agent" and msg.get("text") == text[: inbox.TEXT_MAX_CHARS]:
            return False
    inbox.add_message(circle_id, member, text, lang=lang, direction="out", source="agent", round_n=round_n)
    return True


async def run_member_agents(
    circle_id: str,
    set_id: str | None = None,
    *,
    kinds: tuple[str, ...] = ("consent", "vote"),
    answers: dict[str, str] | None = None,
    answer_source: str = "member",
    publish: Publish = recovery._noop,  # noqa: SLF001
    live_allowed: bool = True,
    provider: Any = None,
) -> dict[str, Any]:
    """Run every pending member agent on the circle's open remedy set.

    ``answers`` are the humans' taps ({member: "yes"|"no"}) for items their agent *asks* about; they are recorded
    with ``answer_source``. Raises ``recovery.RecoveryError`` when there is no open vote."""
    if not member_mandates.enabled(circle_id):
        raise RuntimeError("member mandates are off for this circle")
    answers = {k: v for k, v in (answers or {}).items() if v in ("yes", "no")}
    row = recovery.latest_set(circle_id) if set_id is None else recovery.get_set(circle_id, set_id)
    if row is None:
        raise recovery.RecoveryError("no remedy proposal yet; start the recovery first")
    set_id = row.id
    if row.status != "voting":
        raise recovery.RecoveryError(f"remedy set {set_id} is {row.status}, not open for voting")

    charter = state.load_charter(circle_id)
    rs = recovery.domain_set(row)
    st = recovery.recovery_state(circle_id, rs.round_n)
    decisions: list[dict[str, Any]] = []
    worded = 0
    for kind, member in _pending_items(circle_id, set_id, kinds):
        request = AgentRequest(kind, member, rs)
        mandate = member_mandates.load_mandate(circle_id, member)
        d = decide(request, mandate, charter, st)
        name = _first_name(circle_id, member)
        text, mode = d.reason, "rules"
        if worded < MAX_WORDED_DECISIONS:
            text, mode = await explain_decision(
                name, request, d, provider=provider, live_allowed=live_allowed
            )
            worded += 1
        entry: dict[str, Any] = {
            "member": member,
            "kind": kind,
            "choice": d.choice,
            "reason": d.reason,
            "rule": d.rule,
            "explanation": text,
            "explanation_mode": mode,
            "trace": list(d.trace),
            "recorded": False,
            "source": None,
            "asked": False,
        }
        recorded_as: tuple[str, str] | None = None  # (choice, source)
        if d.choice in ("yes", "no"):
            recorded_as = (d.choice, "agent")
        else:
            entry["asked"] = _post_ask(circle_id, member, rs.round_n, d)
            if member in answers:
                recorded_as = (answers[member], answer_source)
        if recorded_as is not None:
            choice, source = recorded_as
            if kind == "vote":
                await recovery.cast_vote(circle_id, set_id, member, choice, source=source, publish=publish)
            else:
                await recovery.record_consent(
                    circle_id, set_id, member, choice, source=source, publish=publish
                )
            entry.update(recorded=True, source=source, recorded_choice=choice)
        decisions.append(entry)
        verb = {"yes": "says yes", "no": "says no", "ask": "asks the human"}[d.choice]
        await publish(
            {
                "type": "trace",
                "kind": "system",
                "source": "member_agent",
                "text": f"{name}'s agent {verb} ({kind}): {text}",
            }
        )
    await publish({"type": "board"})
    return {
        "ok": True,
        "set_id": set_id,
        "decisions": decisions,
        "asked": list(
            dict.fromkeys(d["member"] for d in decisions if d["choice"] == "ask" and not d["recorded"])
        ),
        "pending": list(dict.fromkeys(d["member"] for d in decisions if not d["recorded"])),
        "labels": {"mandate": mm.MANDATE_LABEL, "signature": mm.SIGNATURE_LABEL},
        **{k: v for k, v in recovery.vote_status(circle_id, set_id).items() if k != "ok"},
    }
