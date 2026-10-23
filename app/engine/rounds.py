"""Round engine: charge contributions, confirm captures, check balances, pay the pot.

Every money path is policy-gated through app.domain.policy. PayPal access goes through the small
accessor functions below (_gateway, _orders, _payouts, _balances, _toolkit, _keys) so tests can
monkeypatch them with fakes and never touch the network. Other builders' modules are imported lazily.

Result dicts always start with "ok". A policy denial is returned as {"ok": False, "denied": reason}
(never raised) so MCP tools and the webhook reconciler can pass it straight through.
"""

from __future__ import annotations

import importlib
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.engine import state
from app.engine.clock import VirtualClock
from app.engine.common import as_utc, iso, money, utcnow
from app.engine.events import Publish
from app.models import Circle, Contribution, LedgerRow, MandateRow, MemberRow, PayoutAttempt, Round

log = logging.getLogger("turnkeeper.engine.rounds")

PAYOUT_FEE_RESERVE = Decimal(
    "0.25"
)  # US domestic Payouts fee per item; the actual fee replaces it when known
MOCK_DECLINE_LABEL = "API decline: sandbox-injected"
REPLAY_PREFIX = "REPLAY-"
SIMULATED_REQUEST_PREFIX = "REPLAY-SIM-"  # == recovery.SIMULATED_REQUEST_PREFIX (simulated Judge Mode rows)
SIMULATED_CAPTURE_LABEL = "Captured (simulated, no PayPal call)"  # == recovery.SIMULATED_CAPTURE_LABEL
PAYOUT_EMAIL_SUBJECT = "Your circle pot is here"

_TOOLKIT: Any = None


# --- accessors (monkeypatched in tests) ---------------------------------------------------------


def _gateway() -> Any:
    return importlib.import_module("app.paypal.gateway").get_gateway()


def _orders() -> Any:
    return importlib.import_module("app.paypal.orders")


def _payouts() -> Any:
    return importlib.import_module("app.paypal.payouts")


def _balances() -> Any:
    return importlib.import_module("app.paypal.balances")


def _toolkit() -> Any:
    """Process-wide ToolkitAdapter built on the shared gateway and its client."""
    global _TOOLKIT
    if _TOOLKIT is None:
        adapter_mod = importlib.import_module("app.paypal.toolkit_adapter")
        gw = _gateway()
        client = getattr(gw, "client", None) or getattr(gw, "_client", None)
        if client is None:
            client = importlib.import_module("app.paypal.client").PayPalClient()
        _TOOLKIT = adapter_mod.ToolkitAdapter(gw, client)
    return _TOOLKIT


def _keys() -> Any:
    return importlib.import_module("app.paypal.keys")


def _policy() -> Any:
    return importlib.import_module("app.domain.policy")


def _mandate_mod() -> Any:
    return importlib.import_module("app.domain.mandate")


def _fees() -> Any:
    return importlib.import_module("app.domain.fees")


def _is_paypal_error(e: BaseException) -> bool:
    try:
        cls = importlib.import_module("app.paypal.client").PayPalError
        if isinstance(e, cls):
            return True
    except (ImportError, AttributeError):
        pass
    return hasattr(e, "issue_codes") and hasattr(e, "name")


def _error_payload(e: BaseException) -> dict[str, Any]:
    codes = list(getattr(e, "issue_codes", None) or [])
    return {"error": str(getattr(e, "name", type(e).__name__)), "issues": codes}


def _authorize(charter: Any, cs: Any, action: str, **kw: Any) -> tuple[Any | None, str | None]:
    """(ticket, None) when allowed, (None, reason) when denied or the policy engine is unavailable."""
    try:
        pol = _policy()
    except ImportError as e:
        log.error("policy engine unavailable: %s", e)
        return None, "policy engine unavailable"
    try:
        return pol.PolicyEngine(charter, cs).authorize(action, **kw), None
    except pol.PolicyDenied as e:
        return None, str(getattr(e, "reason", None) or e)


def _estimate_fee(gross: Decimal) -> Decimal:
    try:
        return money(_fees().estimate_fee(gross))
    except ImportError:
        return money(gross * Decimal("0.0349") + Decimal("0.49"))


def _pot_amount(charter: Any, nets: list[Decimal], advance_net: Decimal, reserve: Decimal) -> Decimal:
    try:
        return money(_fees().pot_amount(charter, nets, advance_net, reserve))
    except ImportError:
        return money(sum(nets, Decimal("0.00")) + advance_net - reserve)


async def _toast(publish: Publish, level: str, text: str) -> None:
    await publish({"type": "toast", "level": level, "text": text})


def _chip(c: Contribution) -> dict[str, Any]:
    return {
        "type": "chip",
        "round_n": c.round_n,
        "member": c.member_handle,
        "status": c.status,
        "order_id": c.order_id,
        "capture_id": c.capture_id,
        "label": c.label,
        "decline_code": c.decline_code,
        "webhook_verified": c.webhook_verified,
        "predicted": c.predicted,
    }


def _result(c: Contribution, ok: bool, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": ok,
        "member": c.member_handle,
        "round_n": c.round_n,
        "status": c.status,
        "order_id": c.order_id,
        "capture_id": c.capture_id,
        "decline_code": c.decline_code,
        "label": c.label,
        "attempt": c.attempt,
        "gross": c.gross,
        "fee": c.fee,
        "net": c.net,
    }
    out.update(extra)
    return out


def _load_contribution(circle_id: str, round_n: int, member: str) -> Contribution | None:
    with session_scope() as db:
        return db.exec(
            select(Contribution).where(
                Contribution.circle_id == circle_id,
                Contribution.round_n == round_n,
                Contribution.member_handle == member,
            )
        ).first()


def _member_row(circle_id: str, member: str) -> MemberRow | None:
    with session_scope() as db:
        return db.exec(
            select(MemberRow).where(MemberRow.circle_id == circle_id, MemberRow.handle == member)
        ).first()


def _save(obj: Any) -> None:
    with session_scope() as db:
        db.add(obj)


# --- collect ------------------------------------------------------------------------------------


async def collect_round(circle_id: str, round_n: int, *, publish: Publish) -> list[dict]:
    """Charge every member of the round in charter order (one FIFO gateway job each)."""
    try:
        charter = state.load_charter(circle_id)
    except LookupError as e:
        return [{"ok": False, "error": str(e)}]
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        return [{"ok": False, "error": f"round {round_n} not found"}]
    if rnd.status == "PENDING":
        rnd.status = "COLLECTING"
        _save(rnd)
        await publish({"type": "board"})

    results: list[dict] = []
    for m in charter.members:
        try:
            gw = _gateway()
            await publish({"type": "queue", "position": int(gw.position())})
        except Exception:  # noqa: BLE001 - the badge is cosmetic
            pass
        try:
            results.append(await charge_member(circle_id, round_n, m.handle, publish=publish))
        except Exception as e:  # noqa: BLE001 - one member's failure must not abort the sweep
            log.exception("charge_member failed for %s round %s", m.handle, round_n)
            results.append(
                {
                    "ok": False,
                    "member": m.handle,
                    "round_n": round_n,
                    "status": "PENDING",
                    "error": type(e).__name__,
                }
            )

    settled = all(r.get("status") in state.SETTLED_CONTRIBUTION for r in results)
    rnd = state.get_round(circle_id, round_n)
    if rnd is not None and settled and rnd.status in ("PENDING", "COLLECTING"):
        rnd.status = "COLLECTED"
        _save(rnd)
        await _toast(publish, "success", f"Round {round_n}: all contributions collected")
    await publish({"type": "board"})
    return results


def _notify_decline(circle_id: str, round_n: int, member: str) -> None:
    """Ask the member when they can pay as soon as the decline happens (B6 inbox; cosmetic, never fatal)."""
    try:
        importlib.import_module("app.engine.recovery").send_decline_notice(circle_id, round_n, member)
    except Exception as e:  # noqa: BLE001 - the notice must never break a charge
        log.warning("decline notice for %s round %s failed: %s", member, round_n, type(e).__name__)


async def charge_member(circle_id: str, round_n: int, member: str, *, publish: Publish) -> dict:
    """Merchant-initiated capture on the member's vault_id (policy-gated, idempotent per attempt)."""
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    contrib = _load_contribution(circle_id, round_n, member)
    mrow = _member_row(circle_id, member)
    if contrib is None or mrow is None:
        return {
            "ok": False,
            "member": member,
            "round_n": round_n,
            "status": None,
            "error": "unknown member or round",
        }
    if contrib.status in state.SETTLED_CONTRIBUTION:
        return _result(contrib, True, note="already settled")

    cs = state.circle_state(circle_id, round_n)
    action = "charge" if contrib.attempt == 0 else "retry_charge"
    _ticket, denied = _authorize(charter, cs, action, member=member, amount=money(charter.amount))
    if denied:
        await _toast(publish, "warn", f"{member}: charge denied by policy ({denied})")
        return _result(contrib, False, denied=denied)

    if not mrow.vault_id or mrow.vault_id.startswith("VAULT_ID_"):
        contrib.label = "No vault id on file (seed TK_SEED_VAULT_IDS)"
        contrib.updated_at = utcnow()
        _save(contrib)
        await publish(_chip(contrib))
        return _result(contrib, False, error="NOT_VAULTED")

    if not get_settings().paypal_configured:
        # No sandbox credentials: never burn an `a{n}` attempt on a call that can only fail with 401.
        contrib.label = "PayPal sandbox not configured"
        contrib.updated_at = utcnow()
        _save(contrib)
        await publish(_chip(contrib))
        return _result(contrib, False, error="PAYPAL_NOT_CONFIGURED")

    mock = bool(state.get_flags(circle).get("ravi_declines")) and member == "ravi"
    prev_attempt, prev_request_id, prev_attempt_at = (
        contrib.attempt,
        contrib.request_id,
        contrib.last_attempt_at,
    )
    attempt = contrib.attempt + 1
    request_id = _keys().order_request_id(circle.run_id, circle_id, round_n, member, attempt)
    contrib.attempt = attempt
    contrib.request_id = request_id
    contrib.last_attempt_at = VirtualClock(circle).now()  # virtual time: the 24h retry spacing reads it
    contrib.updated_at = utcnow()
    _save(contrib)  # persist the key before the call so a crash never replays a cached decline

    orders = _orders()
    try:
        order = await orders.charge_vaulted(
            _gateway(),
            request_id=request_id,
            vault_id=mrow.vault_id,
            amount=money(charter.amount),
            currency=charter.currency,
            reference_id=f"{circle_id}:{round_n}:{member}",
            custom_id=f"{circle.run_id}:{circle.charter_sha256[:8]}",
            description=f"Round {round_n} contribution",
            mock_decline=mock,
        )
    except Exception as e:  # noqa: BLE001 - PayPal errors are mapped, anything else re-raised
        if not _is_paypal_error(e):
            raise
        payload = _error_payload(e)
        codes = payload["issues"]
        status = int(getattr(e, "status", 0) or 0)
        # Only a business rejection with issue codes is a decline. Auth (401), throttling (429), 5xx and
        # transport errors mean PayPal never evaluated the charge (nor the mock header), so they are not
        # labelled as declines and do not consume an attempt.
        declined = "INSTRUMENT_DECLINED" in codes or (
            400 <= status < 500 and status not in (401, 429) and bool(codes)
        )
        if declined:
            contrib.status = "DECLINED"
            contrib.decline_code = codes[0] if codes else payload["error"]
            contrib.decline_source = "api"
            injected = mock and "INSTRUMENT_DECLINED" in codes
            contrib.label = MOCK_DECLINE_LABEL if injected else f"API decline: {contrib.decline_code}"
        else:
            contrib.label = f"PayPal error: {payload['error']}"
            contrib.attempt, contrib.request_id = prev_attempt, prev_request_id
            contrib.last_attempt_at = prev_attempt_at
        contrib.order_id = None
        contrib.capture_id = None
        contrib.updated_at = utcnow()
        _save(contrib)
        log.info("charge %s r%s a%s -> %s (%s)", member, round_n, attempt, contrib.status, contrib.label)
        await publish(_chip(contrib))
        if declined:
            _notify_decline(circle_id, round_n, member)
        return _result(contrib, False, **payload)

    summary = orders.capture_summary(order)
    order_status = str(getattr(summary, "status", None) or order.get("status") or "")
    contrib.order_id = getattr(summary, "order_id", None) or order.get("id")
    contrib.capture_id = getattr(summary, "capture_id", None)
    if order_status != "COMPLETED":
        contrib.label = f"Order {order_status or 'UNKNOWN'}"
        contrib.updated_at = utcnow()
        _save(contrib)
        await publish(_chip(contrib))
        return _result(contrib, False, error="ORDER_NOT_COMPLETED")

    gross = money(getattr(summary, "gross", None) or charter.amount)
    fee_raw = getattr(summary, "fee", None)
    fee = money(fee_raw) if fee_raw is not None else _estimate_fee(gross)
    net_raw = getattr(summary, "net", None)
    net = money(net_raw) if net_raw is not None else money(gross - fee)
    now = utcnow()
    contrib.status = "COMPLETED"
    contrib.gross, contrib.fee, contrib.net = str(gross), str(fee), str(net)
    contrib.decline_code = None
    contrib.decline_source = None
    contrib.label = "Captured" + ("" if fee_raw is not None else " (fee estimated)")
    contrib.updated_at = now
    with session_scope() as db:
        db.add(contrib)
        db.add(
            LedgerRow(
                circle_id=circle_id,
                kind="contribution_net",
                amount=str(net),
                round_n=round_n,
                member_handle=member,
                ref=contrib.capture_id,
                ts=now,
            )
        )
        rnd = db.exec(select(Round).where(Round.circle_id == circle_id, Round.n == round_n)).first()
        if rnd is not None:
            rnd.last_capture_at = now
            if rnd.status == "PENDING":
                rnd.status = "COLLECTING"
            db.add(rnd)
    log.info("charge %s r%s a%s -> COMPLETED order=%s", member, round_n, attempt, contrib.order_id)
    await publish(_chip(contrib))
    await publish({"type": "ticker", "last_capture_at": iso(now), "paid_at": None, "seconds": None})
    return _result(contrib, True)


# --- confirm (toolkit) --------------------------------------------------------------------------


async def confirm_capture(circle_id: str, round_n: int, member: str, *, publish: Publish) -> dict:
    """Confirm a COMPLETED order with the PayPal Agent Toolkit's get_order_details."""
    contrib = _load_contribution(circle_id, round_n, member)
    if contrib is None:
        return {"ok": False, "member": member, "round_n": round_n, "error": "unknown member or round"}
    if not contrib.order_id:
        return _result(contrib, False, error="NO_ORDER", confirmed=False)
    if contrib.order_id.startswith(REPLAY_PREFIX):
        return _result(contrib, True, confirmed=True, replay=True, capture_status="COMPLETED")
    try:
        tk = _toolkit()
        await tk.prepare()
        raw = await tk.run("get_order_details", {"order_id": contrib.order_id})
    except Exception as e:  # noqa: BLE001 - read-only check: report, never raise
        log.warning("get_order_details failed for %s: %s", contrib.order_id, e)
        return _result(contrib, False, confirmed=False, **_error_payload(e))
    order = raw.get("raw", raw) if isinstance(raw, dict) else {}
    status = str(raw.get("status") or order.get("status") or "") if isinstance(raw, dict) else ""
    captures = (((order.get("purchase_units") or [{}])[0]).get("payments") or {}).get("captures") or [{}]
    capture = captures[0] if captures else {}
    capture_id = capture.get("id")
    capture_status = capture.get("status")
    confirmed = status == "COMPLETED" and (
        contrib.capture_id is None or capture_id in (None, contrib.capture_id)
    )
    if confirmed:
        if contrib.capture_id is None and capture_id:
            contrib.capture_id = capture_id
        contrib.label = "Confirmed via toolkit"
        contrib.updated_at = utcnow()
        _save(contrib)
        await publish(_chip(contrib))
    return _result(contrib, True, confirmed=confirmed, order_status=status, capture_status=capture_status)


# --- balances -----------------------------------------------------------------------------------


BALANCES_STALE_NOTE = "PayPal balance is stale (sandbox Balances lags); context only, the ledger is binding"


def _balance_context(bal: Any, data: dict, currency: str) -> dict[str, Any]:
    """{available, last_refresh_time, source: "paypal_balances", stale, age_s} (wrapper fakes may lack it)."""
    fn = getattr(bal, "balance_context", None)
    if fn is None:
        fn = importlib.import_module("app.paypal.balances").balance_context
    return dict(fn(data, currency))


async def available_balance(circle_id: str) -> dict:
    """Per-circle ledger (binding) plus the account-wide PayPal balance when the Balances API answers.

    D1 (2026-10-23): sandbox Balances lags ~2.5 h, so `paypal_balance` is context
    `{available, last_refresh_time, source: "paypal_balances", stale, age_s}`. `paypal_available` stays for the
    MCP tool; it is None when the reading is stale, so a stale figure can never block a payout."""
    charter = state.load_charter(circle_id)
    circle_available, buffer_balance = state.ledger_totals(circle_id)
    paypal_available: Decimal | None = None
    source = "ledger"
    note = None
    context: dict[str, Any] | None = None
    try:
        bal = _balances()
        data = await bal.get_balances(_gateway(), currency=charter.currency)
        context = _balance_context(bal, data, charter.currency)
        state.remember_paypal_balance(charter.currency, context)
        amt = bal.available_amount(data, charter.currency)
        if amt is None:
            note = "balances response had no available amount; ledger is binding"
        elif context.get("stale"):
            note = BALANCES_STALE_NOTE
        else:
            paypal_available = money(amt)
            source = "balances"
    except Exception as e:  # noqa: BLE001 - the ledger is the binding check (PLAN §3 step 10)
        log.warning("balances unavailable (%s); using ledger", type(e).__name__)
        note = f"balances unavailable ({type(e).__name__}); ledger is binding"
    return {
        "ok": True,
        "paypal_available": str(paypal_available) if paypal_available is not None else None,
        "circle_available": str(circle_available),
        "buffer_balance": str(buffer_balance),
        "source": source,
        "note": note,
        "paypal_balance": context,
    }


# --- payout -------------------------------------------------------------------------------------


def _verify_mandate(
    circle: Circle, round_n: int, recipient: str, amount: Decimal, today: Any
) -> tuple[bool, str]:
    with session_scope() as db:
        row = db.exec(
            select(MandateRow).where(MandateRow.circle_id == circle.id).order_by(MandateRow.id.desc())  # type: ignore[attr-defined]
        ).first()
    if row is None:
        return False, "no mandate on file"
    try:
        mm = _mandate_mod()
    except ImportError:
        return False, "mandate module unavailable"
    mandate = mm.Mandate.model_validate_json(row.json)
    ok, reason = mm.verify_mandate(
        mandate,
        get_settings().mandate_hmac_secret,
        charter_sha256=circle.charter_sha256,
        round_n=round_n,
        recipient=recipient,
        amount=amount,
        today=today,
    )
    return bool(ok), str(reason or "")


def _denied(publish_text: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "denied": publish_text or extra.pop("denied", "denied")}
    out.update(extra)
    return out


def _payout_ids(circle: Circle, round_n: int, rev: int, recipient: str) -> tuple[str, str]:
    """(sender_batch_id "tk:{run}:{c}:{r}:r{rev}", sender_item_id). Rev 1 keeps the contract's item id; a
    re-route appends "r{rev}" so the new item never shares an id with the UNCLAIMED one."""
    keys = _keys()
    batch = keys.payout_batch_id(circle.run_id, circle.id, round_n, rev)
    item = keys.payout_item_id(circle.run_id, circle.id, round_n, recipient)
    if rev > 1:
        item = f"{item}r{rev}"
    return batch, item


async def _send_payout(
    *,
    sender_batch_id: str,
    sender_item_id: str,
    receiver: str,
    wallet: str,
    amount: Decimal,
    currency: str,
    note: str,
) -> dict[str, Any]:
    """POST one-item batch, then read the batch and poll its item once.

    Returns {batch_id, item_id, status, fee}; PayPal errors from the create propagate (the caller labels them)."""
    payouts = _payouts()
    gw = _gateway()
    item = payouts.PayoutItem(
        recipient_type="PHONE" if wallet == "VENMO" else "EMAIL",
        receiver=receiver,
        wallet=wallet,
        amount=amount,
        currency=currency,
        note=note,
        sender_item_id=sender_item_id,
    )
    created = await payouts.create_payout(
        gw, sender_batch_id=sender_batch_id, items=[item], email_subject=PAYOUT_EMAIL_SUBJECT
    )
    header = created.get("batch_header", {}) if isinstance(created, dict) else {}
    batch_id = header.get("payout_batch_id")
    status = str(header.get("batch_status") or "PENDING")
    item_id: str | None = None
    fee: Decimal | None = None
    try:
        batch = await payouts.get_payout_batch(gw, batch_id=batch_id)
        first = payouts.first_item(batch)
        if first:
            item_id = first.get("payout_item_id")
            status = str(payouts.item_status(first) or status)
        if item_id:
            polled = await payouts.get_payout_item(gw, item_id=item_id)  # poll the item once
            status = str(payouts.item_status(polled) or status)
            fee_value = (polled.get("payout_item_fee") or {}).get("value")
            if fee_value is not None:
                fee = money(fee_value)
    except Exception as e:  # noqa: BLE001 - the batch exists; webhooks/polling reconcile later
        log.warning("payout item poll failed for batch %s: %s", batch_id, type(e).__name__)
    return {"batch_id": batch_id, "item_id": item_id, "status": status, "fee": fee}


async def pay_round(circle_id: str, round_n: int, *, publish: Publish) -> dict:
    """Pay the pot for a fully collected round: mandate, ledger, balances and policy gates, then Payouts."""
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        return {"ok": False, "error": f"round {round_n} not found"}
    if state.is_frozen(circle):
        return _denied("circle is frozen")
    if rnd.status == "PAID":
        return _denied(
            f"round {round_n} is already paid",
            batch_id=rnd.payout_batch_id,
            item_id=rnd.payout_item_id,
            status=rnd.payout_status,
        )
    if state.live_payout_exists(rnd):
        return _denied(
            f"a live payout already exists for round {round_n} ({rnd.payout_status})",
            batch_id=rnd.payout_batch_id,
            item_id=rnd.payout_item_id,
            status=rnd.payout_status,
        )

    contribs = state.round_contributions(circle_id, round_n)
    by_member = {c.member_handle: c for c in contribs}
    outstanding = [
        m.handle
        for m in charter.members
        if by_member.get(m.handle) is None or by_member[m.handle].status not in state.SETTLED_CONTRIBUTION
    ]
    if outstanding:
        return _denied(f"contributions outstanding: {', '.join(outstanding)}", outstanding=outstanding)
    if any((c.request_id or "").startswith(SIMULATED_REQUEST_PREFIX) for c in contribs):
        # Judge Mode without a live sandbox: never move real sandbox money on simulated contributions.
        return _denied(
            f"round {round_n} was simulated (no PayPal charges); there is nothing real to pay out",
            simulated=True,
        )

    recipient = rnd.recipient_handle
    mrow = _member_row(circle_id, recipient)
    if mrow is None:
        return {"ok": False, "error": f"recipient {recipient} not found"}
    flags = state.get_flags(circle)
    receiver = mrow.payout_receiver
    wallet = mrow.payout_wallet or "PAYPAL"
    unclaimed_test = bool(flags.get("next_payout_unclaimed"))
    if unclaimed_test:
        receiver = f"unclaimed-{circle.run_id}@example.com"  # no PayPal account behind it -> UNCLAIMED
        wallet = "PAYPAL"
    if not receiver:
        return _denied(f"{recipient} has no payout receiver configured")

    nets = [money(c.net) for c in contribs if c.status == "COMPLETED" and c.net]
    advance_net = money(
        sum(
            (
                money(r.amount)
                for r in state.ledger_rows(circle_id)
                if r.kind == "buffer_advance" and r.round_n == round_n
            ),
            Decimal("0.00"),
        )
    )
    amount = _pot_amount(charter, nets, advance_net, PAYOUT_FEE_RESERVE)
    if amount <= 0:
        return _denied("nothing to pay")

    clock = VirtualClock(circle)
    ok, reason = _verify_mandate(circle, round_n, recipient, amount, clock.today())
    if not ok:
        await _toast(publish, "warn", f"Payout blocked: mandate {reason}")
        return _denied(f"mandate: {reason}", amount=str(amount), recipient=recipient)

    circle_available, _buffer = state.ledger_totals(circle_id)
    if circle_available < amount:
        return _denied(
            f"ledger circle_available {circle_available} is below the pot {amount}", amount=str(amount)
        )

    balances = await available_balance(circle_id)
    # paypal_available is None when Balances failed or its reading is stale (D1: ~2.5 h lag in the sandbox), so
    # only a fresh account-wide figure below the pot can refuse; the per-circle ledger above is the binding gate.
    paypal_available = Decimal(balances["paypal_available"]) if balances.get("paypal_available") else None
    if paypal_available is not None and paypal_available < amount:
        return _denied(
            f"PayPal available balance {paypal_available} is below the pot {amount}",
            amount=str(amount),
            source="balances",
        )
    source = balances["source"]

    cs = state.circle_state(circle_id, round_n, paypal_available=paypal_available)
    cs.mandate_ok = True
    _ticket, denied = _authorize(charter, cs, "payout", recipient=recipient, amount=amount)
    if denied:
        await _toast(publish, "warn", f"Payout denied by policy ({denied})")
        return _denied(denied, amount=str(amount), recipient=recipient)

    sender_batch_id, sender_item_id = _payout_ids(circle, round_n, rnd.rev, recipient)
    try:
        sent = await _send_payout(
            sender_batch_id=sender_batch_id,
            sender_item_id=sender_item_id,
            receiver=receiver,
            wallet=wallet,
            amount=amount,
            currency=charter.currency,
            note=f"{charter.name} · Round {round_n} pot",
        )
    except Exception as e:  # noqa: BLE001 - PayPal errors are reported, others re-raised
        if not _is_paypal_error(e):
            raise
        payload = _error_payload(e)
        await _toast(publish, "error", f"Payout failed: {payload['error']}")
        return {
            "ok": False,
            "label": f"Payout failed: {payload['error']}",
            "amount": str(amount),
            "recipient": recipient,
            **payload,
        }
    batch_id, item_id, status, fee = sent["batch_id"], sent["item_id"], sent["status"], sent["fee"]

    now = utcnow()
    paid = status == "SUCCESS"
    with session_scope() as db:
        row = db.exec(select(Round).where(Round.circle_id == circle_id, Round.n == round_n)).first()
        if row is not None:
            row.payout_batch_id = batch_id
            row.payout_item_id = item_id
            row.payout_status = status
            row.payout_amount = str(amount)
            if paid:
                row.status = "PAID"
                row.paid_at = now
            elif row.status in ("PENDING", "COLLECTING"):
                row.status = "COLLECTED"
            db.add(row)
            rnd = row
        db.add(
            LedgerRow(
                circle_id=circle_id,
                kind="payout",
                amount=str(amount),
                round_n=round_n,
                member_handle=recipient,
                ref=item_id or batch_id,
                ts=now,
            )
        )
        db.add(
            LedgerRow(
                circle_id=circle_id,
                kind="payout_fee",
                amount=str(fee if fee is not None else PAYOUT_FEE_RESERVE),
                round_n=round_n,
                member_handle=recipient,
                ref=item_id or batch_id,
                ts=now,
            )
        )
        db.add(
            PayoutAttempt(
                circle_id=circle_id,
                round_n=round_n,
                rev=rnd.rev,
                sender_batch_id=sender_batch_id,
                batch_id=batch_id,
                item_id=item_id,
                receiver_kind="unclaimed_test" if unclaimed_test else "payout_receiver",
                wallet=wallet,
                amount=str(amount),
                fee=str(fee) if fee is not None else None,
                status=status,
                funding="ledger",
                created_at=now,
                updated_at=now,
            )
        )
    if unclaimed_test:
        state.set_flag(circle_id, "next_payout_unclaimed", False)
    if (
        paid
        and rnd.due_at is not None
        and clock.today() <= as_utc(rnd.due_at).date() + timedelta(days=int(charter.grace_days))
    ):
        state.bump_counter(circle_id, "rounds_paid_on_time")

    log.info("payout r%s -> %s batch=%s item=%s amount=%s", round_n, status, batch_id, item_id, amount)
    await publish(
        {
            "type": "payout",
            "round_n": round_n,
            "status": status,
            "batch_id": batch_id,
            "item_id": item_id,
            "amount": str(amount),
            "recipient": recipient,
            "wallet": wallet,
        }
    )
    seconds = (
        int((now - as_utc(rnd.last_capture_at)).total_seconds()) if (paid and rnd.last_capture_at) else None
    )
    await publish(
        {
            "type": "ticker",
            "last_capture_at": iso(rnd.last_capture_at),
            "paid_at": iso(now) if paid else None,
            "seconds": seconds,
        }
    )
    await publish({"type": "board"})
    await _toast(
        publish,
        "success" if paid else "info",
        f"Round {round_n} pot {amount} {charter.currency} -> {recipient}: {status}",
    )
    return {
        "ok": True,
        "batch_id": batch_id,
        "item_id": item_id,
        "status": status,
        "amount": str(amount),
        "recipient": recipient,
        "wallet": wallet,
        "source": source,
        "fee": str(fee) if fee is not None else None,
        "rev": rnd.rev,
        "paypal_balance": balances.get("paypal_balance"),
    }


async def payout_status(circle_id: str, round_n: int) -> dict:
    """Stored payout state, refreshed once from GET payouts-item while it is still in flight."""
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        return {"ok": False, "error": f"round {round_n} not found"}
    status = rnd.payout_status
    item_id = rnd.payout_item_id
    in_flight = status in ("NEW", "PENDING", "PROCESSING", "ONHOLD", "UNCLAIMED")
    if item_id and not item_id.startswith(REPLAY_PREFIX) and in_flight:
        try:
            payouts = _payouts()
            polled = await payouts.get_payout_item(_gateway(), item_id=item_id)
            fresh = str(payouts.item_status(polled) or status)
            if fresh != status:
                with session_scope() as db:
                    row = db.exec(
                        select(Round).where(Round.circle_id == circle_id, Round.n == round_n)
                    ).first()
                    if row is not None:
                        row.payout_status = fresh
                        if fresh == "SUCCESS":
                            row.status = "PAID"
                            row.paid_at = utcnow()
                        db.add(row)
                status = fresh
        except Exception as e:  # noqa: BLE001 - stored status is the fallback
            log.warning("payout status refresh failed for %s: %s", item_id, type(e).__name__)
    return {
        "ok": True,
        "round_n": round_n,
        "status": status,
        "item_id": item_id,
        "batch_id": rnd.payout_batch_id,
        "amount": rnd.payout_amount,
        "recipient": rnd.recipient_handle,
        "round_status": rnd.status,
        "paid_at": iso(rnd.paid_at),
    }


# --- UNCLAIMED re-route (D1, 2026-10-23) ----------------------------------------------------------
#
# Sandbox reality: cancelling an UNCLAIMED item answers 400 BATCH_NOT_COMPLETED while the batch is PROCESSING and
# 500 INTERNAL_SERVICE_ERROR after it is SUCCESS. So the path is: wait for the batch to finish (bounded), try the
# cancel ONCE, and re-route with a NEW batch `r{rev+1}` to the member's other receiver either way.
#
# Ledger (never double-counted):
# - cancel succeeded: `refund` (circle +amount, ref cancel:<item>) then a normal `payout` row for the new item;
# - cancel failed: no refund and no second payout row. The business account fronts the re-route and the first
#   item is shown as "pending return" (PayoutAttempt.return_status) until a cancel or a RETURNED event confirms
#   it; that confirmation changes no circle balance (the circle paid the pot once).
# In both cases the re-route's own Payouts fee is covered by the organizer buffer (`buffer_advance` with no
# member, ref reroute-fee:<batch>) and booked as `payout_fee`, so the circle never goes negative.

REROUTE_WAIT_S = 60.0
# Only a confirmed status counts: an empty or status-less cancel answer is "unconfirmed", never success (D1).
CANCEL_OK_STATUSES = frozenset({"RETURNED", "CANCELED", "CANCELLED", "REFUNDED"})
CANCELLED_LABEL = "Cancelled: funds returned to the circle"
PENDING_RETURN_LABEL = "pending return"


def _receiver_options(mrow: MemberRow | None) -> list[tuple[str, str, str]]:
    """[(receiver_kind, receiver, wallet)] the member can be paid at, preferred first."""
    if mrow is None:
        return []
    out: list[tuple[str, str, str]] = []
    if mrow.payout_receiver:
        out.append(("payout_receiver", mrow.payout_receiver, mrow.payout_wallet or "PAYPAL"))
    if mrow.email and mrow.email != mrow.payout_receiver:
        out.append(("email", mrow.email, "PAYPAL"))
    return out


def _attempt_for(circle_id: str, round_n: int, rev: int) -> PayoutAttempt | None:
    with session_scope() as db:
        return db.exec(
            select(PayoutAttempt).where(
                PayoutAttempt.circle_id == circle_id,
                PayoutAttempt.round_n == round_n,
                PayoutAttempt.rev == rev,
            )
        ).first()


def _cancel_error_text(e: BaseException) -> str:
    status = getattr(e, "status", None)
    name = getattr(e, "name", None) or type(e).__name__
    return f"{status} {name}" if status else str(name)


async def _system_trace(publish: Publish, text: str) -> None:
    await publish({"type": "trace", "kind": "system", "text": text, "source": "payout"})


async def _cancel_unclaimed_once(
    circle_id: str,
    round_n: int,
    rnd: Round,
    prev: PayoutAttempt | None,
    *,
    item_id: str,
    batch_id: str,
    amount: Decimal,
    recipient: str,
    payouts: Any,
    real_payouts: Any,
    gw: Any,
    publish: Publish,
    max_wait_s: float,
    sleep: Any,
    clock: Any,
) -> tuple[bool, str | None, str, dict[str, Any], int]:
    """Steps 1-2 of the re-route: wait for the batch, one cancel attempt (confirmed by re-reading the item), and
    record it. A confirmed cancel books the refund and bumps the round's rev at once.
    Returns (cancel_ok, cancel_error, cancel_label, cancel_view, new_rev)."""
    # 1. wait for the batch to finish (the sandbox refuses a cancel while it is PROCESSING)
    await _system_trace(
        publish,
        f"Round {round_n} payout is UNCLAIMED. Waiting for batch {batch_id} to finish (up to {int(max_wait_s)} s), "
        "then one cancel attempt.",
    )
    wait_kw: dict[str, Any] = {"max_wait_s": max_wait_s, "fetch": payouts.get_payout_batch}
    if sleep is not None:
        wait_kw["sleep"] = sleep
    if clock is not None:
        wait_kw["clock"] = clock
    batch, batch_final = await real_payouts.wait_for_batch_final(gw, batch_id=batch_id, **wait_kw)
    batch_state = real_payouts.batch_status(batch) if batch else "UNKNOWN"

    # 2. exactly one cancel attempt
    cancel_ok = False
    cancel_error: str | None = None
    try:
        res = await payouts.cancel_payout_item(gw, item_id=item_id)
        answered = str(payouts.item_status(res) or "UNKNOWN").upper()
        # Confirm by re-reading the item: the cancel answer alone (possibly empty) never proves the funds are back.
        try:
            after = str(
                payouts.item_status(await payouts.get_payout_item(gw, item_id=item_id)) or "UNKNOWN"
            ).upper()
        except Exception as e:  # noqa: BLE001 - an unreadable item leaves the cancel unconfirmed
            if not _is_paypal_error(e):
                raise
            after = "UNKNOWN"
        cancel_ok = after in CANCEL_OK_STATUSES
        if not cancel_ok:
            cancel_error = (
                f"cancel answered {answered}"
                if answered not in CANCEL_OK_STATUSES and answered != "UNKNOWN"
                else f"cancel unconfirmed ({after})"
            )
    except Exception as e:  # noqa: BLE001 - any cancel failure is recorded honestly, never retried
        cancel_error = _cancel_error_text(e)
        log.info("cancel of UNCLAIMED item %s failed: %s", item_id, cancel_error)
    cancel_label = CANCELLED_LABEL if cancel_ok else real_payouts.CANCEL_UNAVAILABLE_LABEL
    cancel_view = {
        "attempted": True,
        "ok": cancel_ok,
        "status": "canceled" if cancel_ok else "cancel_failed",
        "error": cancel_error,
        "label": cancel_label,
        "batch_status": batch_state,
        "batch_final": batch_final,
    }
    outcome = "returned" if cancel_ok else f"failed ({cancel_error})"
    await _system_trace(
        publish,
        f"Cancel of item {item_id}: {outcome}. "
        + (
            "Funds are back in the circle."
            if cancel_ok
            else f"{cancel_label}; its funds show as pending return."
        ),
    )

    now = utcnow()
    new_rev = rnd.rev + 1
    with session_scope() as db:
        p = db.get(PayoutAttempt, prev.id) if prev else None
        if p is None:
            p = PayoutAttempt(
                circle_id=circle_id,
                round_n=round_n,
                rev=rnd.rev,
                batch_id=batch_id,
                item_id=item_id,
                amount=str(amount),
                created_at=now,
            )
        p.status = "RETURNED" if cancel_ok else "UNCLAIMED"
        p.cancel_status = cancel_view["status"]
        p.cancel_error = cancel_error
        p.label = cancel_label
        p.updated_at = now
        db.add(p)
        row = db.get(Round, rnd.id)
        if cancel_ok and row is not None:
            row.payout_status = "RETURNED"
            row.rev = new_rev  # the old sender_batch_id is spent either way
            db.add(row)
            db.add(
                LedgerRow(
                    circle_id=circle_id,
                    kind="refund",
                    amount=str(amount),
                    round_n=round_n,
                    member_handle=recipient,
                    ref=f"cancel:{item_id}",
                    ts=now,
                )
            )

    return cancel_ok, cancel_error, cancel_label, cancel_view, new_rev


async def reroute_unclaimed(
    circle_id: str,
    round_n: int,
    *,
    publish: Publish,
    max_wait_s: float = REROUTE_WAIT_S,
    sleep: Any = None,
    clock: Any = None,
) -> dict:
    """UNCLAIMED pot -> wait for the batch, one cancel attempt, then a new batch `r{rev+1}` to the other receiver.

    Gates: the item must still be UNCLAIMED (polled once), the member needs another receiver, the policy allows
    `cancel_unclaimed` and then the re-route `payout` (with the first item treated as superseded), the mandate
    verifies, and the buffer can cover the re-route fee. A cancel counts only when re-reading the item confirms
    it. When an earlier call's confirmed cancel left the round RETURNED but its re-route never went out, the next
    call resumes at the send (no second cancel, no second refund). `sleep`/`clock` are injectable for tests."""
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        return {"ok": False, "error": f"round {round_n} not found"}
    if state.is_frozen(circle):
        return _denied("circle is frozen")
    if not rnd.payout_item_id or not rnd.payout_batch_id:
        return _denied(f"round {round_n} has no payout to re-route")
    if rnd.payout_item_id.startswith(REPLAY_PREFIX):
        return _denied(f"round {round_n}'s payout is a replay record; nothing live to re-route")

    payouts = _payouts()
    gw = _gateway()
    item_id, batch_id = rnd.payout_item_id, rnd.payout_batch_id
    status = (rnd.payout_status or "UNKNOWN").upper()
    try:
        status = str(
            payouts.item_status(await payouts.get_payout_item(gw, item_id=item_id)) or status
        ).upper()
    except Exception as e:  # noqa: BLE001 - the stored status is the fallback
        if not _is_paypal_error(e):
            raise
    # Resume: an earlier call cancelled the item (refund booked, rev bumped) but the re-route never went out
    # (policy denial or a PayPal error on the new batch). Skip the cancel and send the re-route now.
    canceled = _attempt_for(circle_id, round_n, rnd.rev - 1) if rnd.rev > 1 else None
    resume = (
        canceled is not None and canceled.cancel_status == "canceled" and canceled.superseded_by_rev is None
    )
    if status != "UNCLAIMED" and not (resume and status in CANCEL_OK_STATUSES):
        return _denied(
            f"round {round_n} payout is {status}; only an UNCLAIMED payout is re-routed", status=status
        )

    recipient = rnd.recipient_handle
    prev = canceled if resume else _attempt_for(circle_id, round_n, rnd.rev)
    used_kind = prev.receiver_kind if prev else None
    options = [o for o in _receiver_options(_member_row(circle_id, recipient)) if o[0] != used_kind]
    if not options:
        return _denied(f"{recipient} has no other payout receiver on file", status=status)
    receiver_kind, receiver, wallet = options[0]
    amount = money(rnd.payout_amount or (prev.amount if prev else None))
    if amount <= 0:
        return _denied("nothing to re-route")

    _circle_available, buffer_balance = state.ledger_totals(circle_id)
    if buffer_balance < PAYOUT_FEE_RESERVE:
        return _denied(f"the buffer ({buffer_balance}) cannot cover the re-route fee {PAYOUT_FEE_RESERVE}")
    cs = state.circle_state(circle_id, round_n)
    if not resume:
        _ticket, denied = _authorize(charter, cs, "cancel_unclaimed", recipient=recipient)
        if denied:
            return _denied(denied)
    ok, reason = _verify_mandate(circle, round_n, recipient, amount, VirtualClock(circle).today())
    if not ok:
        return _denied(f"mandate: {reason}", amount=str(amount), recipient=recipient)

    real_payouts = importlib.import_module("app.paypal.payouts")
    if resume:
        assert canceled is not None
        cancel_ok, cancel_error = True, None
        cancel_label = CANCELLED_LABEL
        cancel_view = {
            "attempted": False,
            "ok": True,
            "status": "canceled",
            "error": None,
            "label": cancel_label,
            "batch_status": None,
            "batch_final": True,
            "resumed": True,
        }
        item_id, batch_id = canceled.item_id or item_id, canceled.batch_id or batch_id
        await _system_trace(
            publish,
            f"Round {round_n}: item {item_id} was already cancelled (funds back in the circle); "
            "sending the re-route that did not go out.",
        )
        new_rev = rnd.rev
        funding = "refund"
    else:
        cancel_ok, cancel_error, cancel_label, cancel_view, new_rev = await _cancel_unclaimed_once(
            circle_id,
            round_n,
            rnd,
            prev,
            item_id=item_id,
            batch_id=batch_id,
            amount=amount,
            recipient=recipient,
            payouts=payouts,
            real_payouts=real_payouts,
            gw=gw,
            publish=publish,
            max_wait_s=max_wait_s,
            sleep=sleep,
            clock=clock,
        )
        funding = "refund" if cancel_ok else "fronted_pending_return"
    now = utcnow()

    # 3. the re-route payout, through the policy with the first item treated as superseded
    cs2 = state.circle_state(circle_id, round_n)
    funded = cs2.circle_available if cancel_ok else money(cs2.circle_available + amount)
    cs2 = cs2.model_copy(
        update={
            "live_payout_exists": False,
            "mandate_ok": True,
            "paypal_available": None,
            "circle_available": funded,
        }
    )
    _ticket, denied = _authorize(charter, cs2, "payout", recipient=recipient, amount=amount)
    if denied:
        await _toast(publish, "warn", f"Re-route denied by policy ({denied})")
        return _denied(denied, cancel=cancel_view, amount=str(amount), recipient=recipient)

    sender_batch_id, sender_item_id = _payout_ids(circle, round_n, new_rev, recipient)
    try:
        sent = await _send_payout(
            sender_batch_id=sender_batch_id,
            sender_item_id=sender_item_id,
            receiver=receiver,
            wallet=wallet,
            amount=amount,
            currency=charter.currency,
            note=f"{charter.name} · Round {round_n} pot (re-sent)",
        )
    except Exception as e:  # noqa: BLE001 - PayPal errors are reported, others re-raised
        if not _is_paypal_error(e):
            raise
        payload = _error_payload(e)
        await _toast(publish, "error", f"Re-route failed: {payload['error']}")
        return {
            "ok": False,
            "label": f"Re-route failed: {payload['error']}",
            "cancel": cancel_view,
            "amount": str(amount),
            "recipient": recipient,
            **payload,
        }

    new_status = sent["status"]
    fee = sent["fee"] if sent["fee"] is not None else PAYOUT_FEE_RESERVE
    paid = new_status == "SUCCESS"
    with session_scope() as db:
        row = db.get(Round, rnd.id)
        if row is not None:
            row.rev = new_rev
            row.payout_batch_id = sent["batch_id"]
            row.payout_item_id = sent["item_id"]
            row.payout_status = new_status
            if paid:
                row.status = "PAID"
                row.paid_at = now
            db.add(row)
        old = db.exec(
            select(PayoutAttempt).where(
                PayoutAttempt.circle_id == circle_id,
                PayoutAttempt.round_n == round_n,
                PayoutAttempt.rev == new_rev - 1,
            )
        ).first()
        if old is not None:
            old.superseded_by_rev = new_rev
            old.return_status = "returned" if cancel_ok else "pending_return"
            if not cancel_ok:
                old.label = f"{real_payouts.CANCEL_UNAVAILABLE_LABEL} · {PENDING_RETURN_LABEL}"
            old.updated_at = now
            db.add(old)
        db.add(
            PayoutAttempt(
                circle_id=circle_id,
                round_n=round_n,
                rev=new_rev,
                sender_batch_id=sender_batch_id,
                batch_id=sent["batch_id"],
                item_id=sent["item_id"],
                receiver_kind=receiver_kind,
                wallet=wallet,
                amount=str(amount),
                fee=str(fee),
                status=new_status,
                funding=funding,
                label=f"Re-sent to the other receiver ({receiver_kind})",
                created_at=now,
                updated_at=now,
            )
        )
        if cancel_ok:
            db.add(
                LedgerRow(
                    circle_id=circle_id,
                    kind="payout",
                    amount=str(amount),
                    round_n=round_n,
                    member_handle=recipient,
                    ref=sent["item_id"] or sent["batch_id"],
                    ts=now,
                )
            )
        db.add(
            LedgerRow(
                circle_id=circle_id,
                kind="buffer_advance",
                amount=str(fee),
                round_n=None,  # not a member advance: pay_round's per-round advance sum must not see it
                member_handle=None,
                ref=f"reroute-fee:{sender_batch_id}",
                ts=now,
            )
        )
        db.add(
            LedgerRow(
                circle_id=circle_id,
                kind="payout_fee",
                amount=str(fee),
                round_n=round_n,
                member_handle=recipient,
                ref=sent["item_id"] or sent["batch_id"],
                ts=now,
            )
        )

    pending = (
        None if cancel_ok else {"amount": str(amount), "item_id": item_id, "label": PENDING_RETURN_LABEL}
    )
    log.info(
        "re-route r%s rev%s -> %s batch=%s item=%s cancel=%s",
        round_n,
        new_rev,
        new_status,
        sent["batch_id"],
        sent["item_id"],
        cancel_view["status"],
    )
    await publish(
        {
            "type": "payout",
            "round_n": round_n,
            "status": new_status,
            "batch_id": sent["batch_id"],
            "item_id": sent["item_id"],
            "amount": str(amount),
            "recipient": recipient,
            "wallet": wallet,
            "rev": new_rev,
            "cancel_status": cancel_view["status"],
            "pending_return": pending,
        }
    )
    await publish({"type": "board"})
    suffix = (
        "" if cancel_ok else f" ({real_payouts.CANCEL_UNAVAILABLE_LABEL}; first item {PENDING_RETURN_LABEL})"
    )
    await _toast(
        publish,
        "success" if paid else "info",
        f"Round {round_n} pot re-sent as r{new_rev} to {recipient}'s other receiver: {new_status}{suffix}",
    )
    return {
        "ok": True,
        "round_n": round_n,
        "rev": new_rev,
        "batch_id": sent["batch_id"],
        "item_id": sent["item_id"],
        "status": new_status,
        "amount": str(amount),
        "recipient": recipient,
        "receiver_kind": receiver_kind,
        "wallet": wallet,
        "fee": str(fee),
        "funding": funding,
        "cancel": cancel_view,
        "previous": {"rev": new_rev - 1, "batch_id": batch_id, "item_id": item_id},
        "pending_return": pending,
    }


def mark_payout_item_returned(item_id: str) -> dict | None:
    """A superseded UNCLAIMED item finally came back (a later cancel or a RETURNED/REFUNDED event).

    Clears "pending return". No circle ledger change: the circle paid this pot once, and the re-route was fronted
    by the business account, which these returned funds make whole. Returns the attempt view, or None when the
    item is not a pending-return attempt (call it from the webhook reconciler for items no round points at)."""
    with session_scope() as db:
        a = db.exec(
            select(PayoutAttempt).where(
                PayoutAttempt.item_id == item_id, PayoutAttempt.return_status == "pending_return"
            )
        ).first()
        if a is None:
            return None
        a.return_status = "returned"
        a.status = "RETURNED"
        a.label = "Returned to the business account (the re-route was fronted)"
        a.updated_at = utcnow()
        db.add(a)
        return {"circle_id": a.circle_id, "round_n": a.round_n, "rev": a.rev, "item_id": a.item_id}


CLAIMED_AFTER_REROUTE_LABEL = "Claimed after re-route: pot paid twice"


def mark_superseded_item_claimed(item_id: str) -> dict | None:
    """A superseded (re-routed) item was claimed after all (PAYMENT.PAYOUTS-ITEM.SUCCEEDED): the member was paid
    twice and the business account lost the fronted amount. Records it on the attempt (status SUCCESS,
    return_status ``claimed_after_reroute``) so the board stops showing "pending return". Returns the attempt view
    (``already`` True when it was recorded before), or None when the item is not a superseded attempt."""
    with session_scope() as db:
        a = db.exec(
            select(PayoutAttempt).where(
                PayoutAttempt.item_id == item_id,
                PayoutAttempt.superseded_by_rev != None,  # noqa: E711 - SQL expression
            )
        ).first()
        if a is None:
            return None
        already = a.return_status == "claimed_after_reroute"
        if not already:
            a.status = "SUCCESS"
            a.return_status = "claimed_after_reroute"
            a.label = CLAIMED_AFTER_REROUTE_LABEL
            a.updated_at = utcnow()
            db.add(a)
        return {
            "circle_id": a.circle_id,
            "round_n": a.round_n,
            "rev": a.rev,
            "item_id": a.item_id,
            "amount": a.amount,
            "already": already,
        }


# --- simulated clean round (Judge Mode without a live sandbox) -------------------------------------


async def simulate_clean_round(circle_id: str, round_n: int, *, publish: Publish) -> dict:
    """Settle every PENDING contribution of the round with labelled REPLAY- ids and no PayPal call.

    The no-credentials stand-in for a clean live round (the story's "Clean Round 4"). Request ids start with
    SIMULATED_REQUEST_PREFIX, so pay_round refuses to move real money for this round."""
    circle = state.load_circle(circle_id)
    charter = state.charter_of(circle)
    rnd = state.get_round(circle_id, round_n)
    if rnd is None:
        return {"ok": False, "error": f"round {round_n} not found"}
    if rnd.status in ("PAID", "VOID"):
        return {"ok": False, "error": f"round {round_n} is {rnd.status}"}
    amount = money(charter.amount)
    fee = _estimate_fee(amount)
    net = money(amount - fee)
    now = utcnow()
    virtual_now = VirtualClock(circle).now()
    captured: list[str] = []
    chips: list[dict[str, Any]] = []
    with session_scope() as db:
        rows = db.exec(
            select(Contribution).where(Contribution.circle_id == circle_id, Contribution.round_n == round_n)
        ).all()
        for c in rows:
            if c.status != "PENDING":
                continue
            handle = c.member_handle.upper()
            c.status = "COMPLETED"
            c.attempt = max(1, c.attempt)
            c.order_id = f"REPLAY-ORD-{round_n}-{handle}"
            c.capture_id = f"REPLAY-CAP-{round_n}-{handle}"
            c.gross, c.fee, c.net = str(amount), str(fee), str(net)
            c.decline_code = None
            c.decline_source = None
            c.label = SIMULATED_CAPTURE_LABEL
            c.request_id = f"{SIMULATED_REQUEST_PREFIX}{circle.run_id}-{round_n}-{c.member_handle}"
            c.last_attempt_at = virtual_now
            c.updated_at = now
            db.add(c)
            db.add(
                LedgerRow(
                    circle_id=circle_id,
                    kind="contribution_net",
                    amount=str(net),
                    round_n=round_n,
                    member_handle=c.member_handle,
                    ref=c.capture_id,
                    ts=now,
                )
            )
            captured.append(c.member_handle)
            chips.append(_chip(c))
        settled = bool(rows) and all(c.status in state.SETTLED_CONTRIBUTION for c in rows)
        live_round = db.get(Round, rnd.id)
        if live_round is not None:
            if captured:
                live_round.last_capture_at = now
            live_round.status = "COLLECTED" if settled else "COLLECTING"
            db.add(live_round)
    for chip in chips:
        await publish(chip)
    await _system_trace(
        publish,
        f"Judge: round {round_n} simulated without PayPal ({len(captured)} captures labelled REPLAY-). "
        "Payout skipped: simulated charges never move real sandbox money.",
    )
    await _toast(publish, "info", f"Round {round_n} simulated: all contributions settled (no sandbox call)")
    await publish({"type": "board"})
    return {"ok": True, "round_n": round_n, "captured": captured, "settled": settled, "simulated": True}
