"""Load app/fixtures/demo_circle.json and clone it into a fresh Circle for a session.

Rounds listed in `replay.settled_rounds` are pre-settled with REPLAY- prefixed IDs (no PayPal call is
ever made for them). The next round is PENDING with the forecast's "predicted" badges applied.
"""

from __future__ import annotations

import importlib
import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlmodel import Session as DbSession

from app.config import get_settings
from app.engine.common import money, money_str, parse_iso, utcnow
from app.models import Circle, Contribution, LedgerRow, MandateRow, MemberRow, Round

log = logging.getLogger("turnkeeper.engine.fixture")

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "demo_circle.json"
VAULT_PLACEHOLDER_PREFIX = "VAULT_ID_"
MANDATE_GRACE_DAYS = 30


class SeedSettings(BaseSettings):
    """Seed overrides, read through pydantic-settings exactly like app.config (never os.environ)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    seed_vault_ids: str = Field(default="", alias="TK_SEED_VAULT_IDS")


def _charter_mod() -> Any:
    return importlib.import_module("app.domain.charter")


@lru_cache(maxsize=1)
def load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def seed_overrides() -> dict[str, dict[str, Any]]:
    """TK_SEED_VAULT_IDS as {handle: {vault_id, customer_id?, payout_receiver?, payout_wallet?, sandbox_account?}}."""
    raw = SeedSettings().seed_vault_ids.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("TK_SEED_VAULT_IDS is not valid JSON; using fixture placeholders")
        return {}
    if not isinstance(data, dict):
        log.warning("TK_SEED_VAULT_IDS must be a JSON object; using fixture placeholders")
        return {}
    out: dict[str, dict[str, Any]] = {}
    keys = ("vault_id", "customer_id", "payout_receiver", "payout_wallet", "sandbox_account")
    for handle, value in data.items():
        if isinstance(value, str):
            out[str(handle)] = {"vault_id": value}
        elif isinstance(value, dict):
            out[str(handle)] = {k: value[k] for k in keys if value.get(k)}
    return out


def is_placeholder_vault(vault_id: str | None) -> bool:
    return not vault_id or vault_id.startswith(VAULT_PLACEHOLDER_PREFIX)


def fixture_charter() -> Any:
    return _charter_mod().Charter.model_validate(load_fixture()["charter"])


def _due_at(charter: Any, n: int) -> datetime:
    d: date = _charter_mod().due_date(charter, n)
    return datetime.combine(d, time(9, 0), tzinfo=UTC)


def _fmt(template: str, *, round_n: int, handle: str) -> str:
    return template.format(round=round_n, HANDLE=handle.upper(), handle=handle)


def _create_mandate(db: DbSession, circle: Circle, charter: Any, now: datetime) -> bool:
    """Create and sign the demo mandate (organizer-approved, server-signed). Skipped if B2 is absent."""
    try:
        mandate_mod = importlib.import_module("app.domain.mandate")
    except ImportError as e:
        log.warning("mandate module unavailable, fixture clone has no mandate: %s", e)
        return False
    cm = _charter_mod()
    total = cm.rounds_total(charter)
    expires = cm.due_date(charter, total) + timedelta(days=MANDATE_GRACE_DAYS)
    mandate = mandate_mod.Mandate(
        charter_sha256=circle.charter_sha256,
        recipients=list(charter.rotation),
        cap_gross_per_round=money(Decimal(charter.amount) * len(charter.members)),
        expires=expires,
        approved_at=now,
        approved_by="organizer (demo fixture)",
        user_agent="fixture",
    )
    signed = mandate_mod.sign_mandate(mandate, get_settings().mandate_hmac_secret)
    db.add(MandateRow(circle_id=circle.id, json=signed.model_dump_json(), approved_at=now))
    return True


def clone_fixture(db: DbSession, *, circle_id: str, run_id: str, now: datetime | None = None) -> Circle:
    """Insert a complete clone of the demo circle (circle, members, rounds, contributions, ledger, mandate)."""
    fx = load_fixture()
    cm = _charter_mod()
    charter = cm.Charter.model_validate(fx["charter"])
    now = now or utcnow()
    replay = fx.get("replay", {})
    settled = [int(n) for n in replay.get("settled_rounds", [])]
    amount = money(charter.amount)
    replay_fee = money(replay.get("fee", "0.00"))
    replay_net = money(amount - replay_fee)
    payout_fee = money(replay.get("payout_fee", "0.00"))
    late = {h: {int(n) for n in rounds} for h, rounds in replay.get("late", {}).items()}
    late_days = int(replay.get("late_days", 2))
    ids = replay.get("ids", {})
    predicted = {int(k): set(v) for k, v in fx.get("forecast", {}).get("predicted", {}).items()}
    overrides = seed_overrides()
    seed_vaults = fx.get("seed", {}).get("vault_ids", {})

    circle = Circle(
        id=circle_id,
        run_id=run_id,
        name=charter.name,
        charter_json=cm.canonical_json(charter),
        charter_sha256=cm.charter_sha256(charter),
        fee_policy=charter.fee_policy,
        status="active",
        is_fixture_clone=True,
        virtual_now=parse_iso(fx.get("clock", {}).get("virtual_now", now.isoformat())),
        created_at=now,
        flags_json=json.dumps(fx.get("flags", {})),
        counters_json=json.dumps(
            {"organizer_messages_typed": 0, "rounds_paid_on_time": len(settled), "shortfalls_covered": 0}
        ),
    )
    db.add(circle)

    rotation_pos = {h: i for i, h in enumerate(charter.rotation, start=1)}
    for m in charter.members:
        ov = overrides.get(m.handle, {})
        db.add(
            MemberRow(
                circle_id=circle_id,
                handle=m.handle,
                name=m.name,
                lang=m.lang,
                email=m.email,
                payday_rule=m.payday_rule,
                vault_id=ov.get("vault_id")
                or seed_vaults.get(m.handle)
                or f"{VAULT_PLACEHOLDER_PREFIX}{m.handle.upper()}",
                customer_id=ov.get("customer_id"),
                payout_wallet=ov.get("payout_wallet") or m.payout_wallet,
                payout_receiver=ov.get("payout_receiver") or m.payout_receiver,
                sandbox_account=ov.get("sandbox_account"),
                rotation_pos=rotation_pos.get(m.handle, 0),
                track_record_rounds=m.track_record_rounds,
            )
        )

    db.add(
        LedgerRow(
            circle_id=circle_id,
            kind="buffer_seed",
            amount=money_str(charter.buffer.seed),
            ref="fixture:seed",
            ts=now,
        )
    )

    total_rounds = cm.rounds_total(charter)
    for n in range(1, total_rounds + 1):
        due_at = _due_at(charter, n)
        recipient = cm.recipient_for_round(charter, n)
        rnd = Round(circle_id=circle_id, n=n, due_at=due_at, recipient_handle=recipient, status="PENDING")
        if n in settled:
            last_capture = due_at
            for m in charter.members:
                captured_at = due_at + (
                    timedelta(days=late_days) if n in late.get(m.handle, set()) else timedelta(0)
                )
                last_capture = max(last_capture, captured_at)
                capture_id = _fmt(
                    ids.get("capture", "REPLAY-CAP-{round}-{HANDLE}"), round_n=n, handle=m.handle
                )
                db.add(
                    Contribution(
                        circle_id=circle_id,
                        round_n=n,
                        member_handle=m.handle,
                        order_id=_fmt(
                            ids.get("order", "REPLAY-ORD-{round}-{HANDLE}"), round_n=n, handle=m.handle
                        ),
                        capture_id=capture_id,
                        gross=str(amount),
                        fee=str(replay_fee),
                        net=str(replay_net),
                        status="COMPLETED",
                        attempt=1,
                        webhook_verified=True,
                        label="Captured late (replay)"
                        if n in late.get(m.handle, set())
                        else "Captured (replay)",
                        request_id=f"REPLAY-{run_id}-{n}-{m.handle}",
                        created_at=captured_at,
                        updated_at=captured_at,
                    )
                )
                db.add(
                    LedgerRow(
                        circle_id=circle_id,
                        kind="contribution_net",
                        amount=str(replay_net),
                        round_n=n,
                        member_handle=m.handle,
                        ref=capture_id,
                        ts=captured_at,
                    )
                )
            pot = money(replay_net * len(charter.members) - payout_fee)
            item_id = _fmt(ids.get("item", "REPLAY-ITEM-{round}"), round_n=n, handle=recipient)
            paid_at = last_capture + timedelta(seconds=42)
            rnd.status = "PAID"
            rnd.payout_batch_id = _fmt(ids.get("batch", "REPLAY-BATCH-{round}"), round_n=n, handle=recipient)
            rnd.payout_item_id = item_id
            rnd.payout_status = "SUCCESS"
            rnd.payout_amount = str(pot)
            rnd.last_capture_at = last_capture
            rnd.paid_at = paid_at
            db.add(
                LedgerRow(
                    circle_id=circle_id,
                    kind="payout",
                    amount=str(pot),
                    round_n=n,
                    member_handle=recipient,
                    ref=item_id,
                    ts=paid_at,
                )
            )
            db.add(
                LedgerRow(
                    circle_id=circle_id,
                    kind="payout_fee",
                    amount=str(payout_fee),
                    round_n=n,
                    member_handle=recipient,
                    ref=item_id,
                    ts=paid_at,
                )
            )
        else:
            for m in charter.members:
                db.add(
                    Contribution(
                        circle_id=circle_id,
                        round_n=n,
                        member_handle=m.handle,
                        status="PENDING",
                        attempt=0,
                        predicted=m.handle in predicted.get(n, set()),
                        created_at=now,
                        updated_at=now,
                    )
                )
        db.add(rnd)

    _create_mandate(db, circle, charter, now)
    db.flush()
    return circle
