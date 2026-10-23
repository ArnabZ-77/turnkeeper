"""Operator script (NOT a test): the real Round 3 demo against the live PayPal SANDBOX, in-process.

It drives the app's own engine with the seeded vault ids (TK_SEED_VAULT_IDS in .env):
  1. reset a session circle (a fresh fixture clone with a new run id; `--sid` resets an existing session),
  2. collect round 3 with `ravi_declines` on (5 real merchant-initiated captures + 1 sandbox-injected decline),
     then confirm each capture with the Agent Toolkit's get_order_details,
  3. run the recovery in RULES mode (no LLM): Ravi's reply -> commitment -> default remedies -> vote -> consents
     -> organizer approval -> execution (buffer advance, turn swap, toolkit invoice),
  4. pay the round 3 pot through every gate (mandate, ledger, policy; Balances as context) and poll the item.

Prints one JSON line per step with the real sandbox ids (order, capture, invoice, batch, item), then PASS/FAIL.
Exit 0 on PASS, 1 on FAIL (also for missing credentials or unseeded vault ids). Secrets, tokens and payout
receivers are never printed.

Usage:
    .venv/Scripts/python -I scripts/live_round3.py [--sid <tk_sid>] [--poll 90] [--allow-pending] [--events]
"""

# ruff: noqa: E402  (the repo root goes on sys.path before any app import; python -I safe)
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.smoke._common import emit as _emit  # also chdirs to the repo root so .env is found

ROUND_N = 3
MEMBER = "ravi"
FINAL_ITEM = frozenset({"SUCCESS", "FAILED", "RETURNED", "BLOCKED", "REFUNDED", "REVERSED", "UNCLAIMED"})
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _clean(value: Any) -> Any:
    """No email address ever reaches stdout (payout receivers, payer emails inside error texts)."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return _EMAIL.sub("<email>", value)
    return value


def emit(step: str, **fields: Any) -> None:
    _emit(step, **_clean(fields))


class Failure(Exception):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sid", help="reset this existing session (tk_sid) instead of creating a new one")
    p.add_argument(
        "--poll", type=float, default=90.0, help="seconds to poll the payout item for a final status"
    )
    p.add_argument("--allow-pending", action="store_true", help="PASS even if the item is still PENDING")
    p.add_argument("--events", action="store_true", help="also print every engine event as a JSON line")
    return p.parse_args(argv)


async def run_live(
    args: argparse.Namespace,
    *,
    after_create: Callable[[str], None] | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> bool:
    """The whole demo. Returns True on PASS; raises Failure with the reason otherwise."""
    from app.config import get_settings
    from app.db import init_db
    from app.engine import inbox, recovery, rounds, sessions, state
    from app.routes.circles import circle_vault_seeded

    settings = get_settings()
    if not settings.paypal_configured:
        raise Failure("PayPal sandbox credentials not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)")
    emit("config", ok=True, base_url=settings.paypal_base_url, env=settings.paypal_env)
    init_db()

    async def publish(event: dict) -> None:
        if args.events:
            emit("event", **{k: v for k, v in event.items() if k != "ts"})

    # 1. a fresh circle
    if args.sid:
        sess = sessions.reset_session(args.sid)
    else:
        sess = sessions.create_session(enforce_cap=False)
    cid = sess.circle_id
    if after_create is not None:
        after_create(cid)
    seeded = circle_vault_seeded(cid)
    emit("reset", ok=seeded, circle_id=cid, run_id=sess.run_id, vault_seeded=seeded)
    if not seeded:
        raise Failure("members not vaulted: set TK_SEED_VAULT_IDS in .env (see README)")
    state.set_flag(cid, "ravi_declines", True)
    state.set_flag(cid, "next_payout_unclaimed", False)

    # 2. collect round 3 and confirm the captures with the toolkit
    started = time.monotonic()
    results = await rounds.collect_round(cid, ROUND_N, publish=publish)
    for r in results:
        emit(
            "charge",
            ok=r.get("status") == ("DECLINED" if r.get("member") == MEMBER else "COMPLETED"),
            member=r.get("member"),
            status=r.get("status"),
            order_id=r.get("order_id"),
            capture_id=r.get("capture_id"),
            gross=r.get("gross"),
            fee=r.get("fee"),
            net=r.get("net"),
            decline_code=r.get("decline_code"),
            label=r.get("label"),
            error=r.get("error"),
        )
    by = {r.get("member"): r for r in results}
    if by.get(MEMBER, {}).get("status") != "DECLINED":
        raise Failure(f"{MEMBER} was not declined: {by.get(MEMBER, {}).get('status')}")
    missing = [m for m, r in by.items() if m != MEMBER and r.get("status") != "COMPLETED"]
    if missing:
        raise Failure(f"captures did not complete for: {', '.join(missing)}")
    for m in by:
        if m == MEMBER:
            continue
        conf = await rounds.confirm_capture(cid, ROUND_N, m, publish=publish)
        emit(
            "confirm",
            ok=bool(conf.get("confirmed")),
            member=m,
            order_id=conf.get("order_id"),
            capture_id=conf.get("capture_id"),
            order_status=conf.get("order_status"),
            capture_status=conf.get("capture_status"),
        )

    # 3. recovery, rules mode (no LLM anywhere in this script)
    circle = state.load_circle(cid)
    from app.engine.clock import VirtualClock

    reply = inbox.add_message(
        cid, MEMBER, inbox.DEFAULT_RAVI_REPLY, lang="hi", direction="in", source="judge"
    )
    payday = inbox.parse_payday(inbox.DEFAULT_RAVI_REPLY, today=VirtualClock(circle).today())
    emit(
        "reply",
        ok=True,
        member=MEMBER,
        message_id=reply.get("id"),
        payday=payday.isoformat() if payday else None,
    )
    rec = await recovery.run_rules_recovery(cid, ROUND_N, MEMBER, publish=publish)
    set_id = rec.get("set_id")
    emit("recovery", ok=bool(rec.get("ok")), mode="rules", set_id=set_id, error=rec.get("error"))
    if not rec.get("ok") or not set_id:
        raise Failure(f"rules recovery failed: {rec.get('error') or rec}")
    row = recovery.get_set(cid, set_id)
    for handle in recovery.domain_set(row).affected_members():
        await recovery.record_consent(cid, set_id, handle, "yes", source="judge", publish=publish)
    charter = state.load_charter(cid)
    affected = set(recovery.domain_set(row).affected_members()) | {row.member}
    status = recovery.vote_status(cid, set_id)
    voters = [m.handle for m in charter.members if m.handle not in affected]
    voters += [m.handle for m in charter.members if m.handle in affected]
    for handle in voters:
        if status["votes"]["passed"]:
            break
        status = await recovery.cast_vote(cid, set_id, handle, "yes", source="judge", publish=publish)
    emit(
        "vote",
        ok=bool(status["votes"]["passed"]),
        yes=status["votes"]["yes"],
        needed=status["votes"]["needed"],
        consents=status["consents"]["given"],
    )
    approved = await recovery.approve(cid, set_id, by=recovery.ORGANIZER, execute=False, publish=publish)
    if approved.get("ok") is False:
        raise Failure(f"approval refused: {approved.get('denied')}")
    executed = await recovery.execute_remedies(cid, set_id, publish=publish, pay_after=False)
    for res in executed.get("results") or []:
        emit(
            "remedy",
            ok=bool(res.get("ok")),
            type=res.get("type"),
            amount=res.get("amount"),
            invoice_id=res.get("invoice_id"),
            simulated=res.get("simulated"),
            label=res.get("label"),
            error=res.get("error"),
        )
    if not executed.get("ok"):
        raise Failure(f"remedy execution failed: {executed.get('denied') or executed.get('status')}")

    # 4. the pot, then poll the item
    paid = await rounds.pay_round(cid, ROUND_N, publish=publish)
    ctx = paid.get("paypal_balance") or {}
    emit(
        "payout",
        ok=bool(paid.get("ok")),
        batch_id=paid.get("batch_id"),
        item_id=paid.get("item_id"),
        status=paid.get("status"),
        amount=paid.get("amount"),
        recipient=paid.get("recipient"),
        fee=paid.get("fee"),
        funds_check=paid.get("source"),
        paypal_balance=ctx.get("available"),
        paypal_balance_refreshed=ctx.get("last_refresh_time"),
        paypal_balance_stale=ctx.get("stale"),
        denied=paid.get("denied"),
        error=paid.get("error"),
    )
    if not paid.get("ok"):
        raise Failure(f"payout refused: {paid.get('denied') or paid.get('label') or paid.get('error')}")
    final = str(paid.get("status"))
    deadline = time.monotonic() + max(0.0, args.poll)
    while final not in FINAL_ITEM and time.monotonic() < deadline:
        await sleep(5.0)
        polled = await rounds.payout_status(cid, ROUND_N)
        final = str(polled.get("status"))
        emit("payout_poll", ok=True, item_id=polled.get("item_id"), status=final)
    board = state.board_model(cid)
    emit(
        "summary",
        ok=final == "SUCCESS" or (args.allow_pending and final in ("PENDING", "PROCESSING", "NEW")),
        circle_id=cid,
        run_id=sess.run_id,
        payout_status=final,
        seconds=int(time.monotonic() - started),
        circle_available=board["circle"]["circle_available"],
        buffer_balance=board["circle"]["buffer_balance"],
        rotation=board["circle"]["rotation"],
    )
    if final == "SUCCESS":
        return True
    if args.allow_pending and final in ("PENDING", "PROCESSING", "NEW"):
        return True
    raise Failure(f"payout item is {final}")


async def _main(args: argparse.Namespace) -> bool:
    try:
        return await run_live(args)
    except Failure as e:
        emit("result", ok=False, reason=str(e))
        return False
    finally:
        try:
            from app.paypal.gateway import get_gateway, set_gateway

            gw = get_gateway()
            await gw.stop()
            client = getattr(gw, "client", None) or getattr(gw, "_client", None)
            if client is not None:
                await client.aclose()
            set_gateway(None)  # the closed client must never be reused in this process
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        ok = asyncio.run(_main(args))
    except KeyboardInterrupt:
        emit("result", ok=False, reason="interrupted")
        ok = False
    except Exception as e:  # noqa: BLE001 - an operator script reports, never dumps a traceback with data
        emit("result", ok=False, reason=f"{type(e).__name__}: {str(e)[:300]}")
        ok = False
    print("PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
