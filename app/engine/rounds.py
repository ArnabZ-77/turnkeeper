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
from app.models import Circle, Contribution, LedgerRow, MandateRow, MemberRow, Round

log = logging.getLogger("turnkeeper.engine.rounds")

PAYOUT_FEE_RESERVE = Decimal(
    "0.25"
)  # US domestic Payouts fee per item; the actual fee replaces it when known
MOCK_DECLINE_LABEL = "API decline: sandbox-injected"
REPLAY_PREFIX = "REPLAY-"
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


async def available_balance(circle_id: str) -> dict:
    """Per-circle ledger (binding) plus the account-wide PayPal balance when the Balances API answers."""
    charter = state.load_charter(circle_id)
    circle_available, buffer_balance = state.ledger_totals(circle_id)
    paypal_available: Decimal | None = None
    source = "ledger"
    note = None
    try:
        bal = _balances()
        data = await bal.get_balances(_gateway(), currency=charter.currency)
        amt = bal.available_amount(data, charter.currency)
        if amt is not None:
            paypal_available = money(amt)
            source = "balances"
        else:
            note = "balances response had no available amount; ledger is binding"
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

    keys = _keys()
    payouts = _payouts()
    sender_batch_id = keys.payout_batch_id(circle.run_id, circle_id, round_n, rnd.rev)
    sender_item_id = keys.payout_item_id(circle.run_id, circle_id, round_n, recipient)
    item = payouts.PayoutItem(
        recipient_type="PHONE" if wallet == "VENMO" else "EMAIL",
        receiver=receiver,
        wallet=wallet,
        amount=amount,
        currency=charter.currency,
        note=f"{charter.name} · Round {round_n} pot",
        sender_item_id=sender_item_id,
    )
    gw = _gateway()
    try:
        created = await payouts.create_payout(
            gw, sender_batch_id=sender_batch_id, items=[item], email_subject=PAYOUT_EMAIL_SUBJECT
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
