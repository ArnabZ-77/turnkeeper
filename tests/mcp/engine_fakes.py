"""Stateful stand-ins for B4's engine (app.engine.rounds / app.engine.state), B2's PolicyDenied and B1's
PayPalError, installed into sys.modules so the MCP tools' lazy imports pick them up. No network, no DB."""

from __future__ import annotations

import datetime as dt
import sys
import types
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

DEMO_CIRCLE = "abc123"
OTHER_CIRCLE = "zzz999"
MEMBERS = ("asha", "ravi", "meena", "tomas", "lin", "dev")
NAMES = {
    "asha": "Asha Patel",
    "ravi": "Ravi Kumar",
    "meena": "Meena Iyer",
    "tomas": "Tomas Alvarez",
    "lin": "Lin Chen",
    "dev": "Dev Shah",
}
ROTATION = ["meena", "asha", "dev", "lin", "tomas", "ravi"]


class PolicyDenied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PayPalError(Exception):
    def __init__(self, status: int, name: str, message: str, details: list[dict] | None = None) -> None:
        super().__init__(f"{status} {name}: {message}")
        self.status = status
        self.name = name
        self.message = message
        self.debug_id = "dbg-test"
        self.details = details or []
        self.raw = {}

    @property
    def issue_codes(self) -> list[str]:
        return [d["issue"] for d in self.details if "issue" in d]


class FakeMember(BaseModel):
    handle: str
    name: str
    lang: str = "en"
    email: str | None = None
    payday_rule: str | None = None
    payout_wallet: str = "PAYPAL"
    payout_receiver: str | None = None
    track_record_rounds: int = 0


class FakeBuffer(BaseModel):
    seed: Decimal = Decimal("250.00")
    source: str = "organizer"
    retention_pct: int = 0


class FakeCharter(BaseModel):
    name: str = "Temple Circle A"
    currency: str = "USD"
    amount: Decimal = Decimal("200.00")
    cadence: str = "monthly"
    start_date: dt.date = dt.date(2026, 11, 1)
    members: list[FakeMember]
    rotation: list[str] = ROTATION
    fee_policy: str = "net_pot"
    buffer: FakeBuffer = FakeBuffer()
    grace_days: int = 3
    quorum: str = "4/6"
    remedy_menu: list[str] = ["retry_on_date", "turn_swap", "buffer_advance", "repay_by_invoice"]


class FakeEngine:
    """Records every engine call; contributions move PENDING -> COMPLETED/DECLINED on collect."""

    def __init__(
        self,
        *,
        declines: tuple[str, ...] = (),
        pay_error: Exception | None = None,
        charge_error: Exception | None = None,
        paypal_available: str | None = "900.00",
    ) -> None:
        self.declines = set(declines)
        self.pay_error = pay_error
        self.charge_error = charge_error
        self.paypal_available = paypal_available
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.published: list[dict[str, Any]] = []
        self.contributions: dict[tuple[int, str], dict[str, Any]] = {}
        self.payouts: dict[int, dict[str, Any]] = {}
        self.demo_circles = {DEMO_CIRCLE}
        for member in MEMBERS:
            for n in (1, 2):
                self.contributions[(n, member)] = self._row(n, member, "COMPLETED", replay=True)

    # -- helpers ------------------------------------------------------------------------------------------
    def _row(
        self, n: int, member: str, status: str, *, replay: bool = False, attempt: int = 1
    ) -> dict[str, Any]:
        prefix = "REPLAY-" if replay else ""
        completed = status == "COMPLETED"
        return {
            "round_n": n,
            "member": member,
            "status": status,
            "order_id": f"{prefix}ORD-{member}-{n}" if completed else None,
            "capture_id": f"{prefix}CAP-{member}-{n}" if completed else None,
            "gross": Decimal("200.00") if completed else None,
            "fee": Decimal("7.47") if completed else None,
            "net": Decimal("192.53") if completed else None,
            "decline_code": "INSTRUMENT_DECLINED" if status == "DECLINED" else None,
            "decline_source": "api" if status == "DECLINED" else None,
            "attempt": attempt,
            "webhook_verified": completed and replay,
            "predicted": False,
            "label": "API decline: sandbox-injected" if status == "DECLINED" else None,
        }

    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [kw for n, kw in self.calls if n == name]

    def call_names(self) -> list[str]:
        return [n for n, _ in self.calls]

    # -- app.engine.rounds -----------------------------------------------------------------------------------
    async def collect_round(self, circle_id: str, round_n: int, *, publish: Any) -> list[dict[str, Any]]:
        self.calls.append(("collect_round", {"circle_id": circle_id, "round_n": round_n}))
        results = []
        for member in MEMBERS:
            existing = self.contributions.get((round_n, member))
            if existing and existing["status"] == "COMPLETED":
                row = existing
            else:
                status = "DECLINED" if member in self.declines else "COMPLETED"
                row = self._row(round_n, member, status)
                self.contributions[(round_n, member)] = row
            await publish({"type": "chip", "round_n": round_n, "member": member, "status": row["status"]})
            results.append(
                {k: row[k] for k in ("member", "status", "order_id", "capture_id", "decline_code", "label")}
            )
        return results

    async def charge_member(
        self, circle_id: str, round_n: int, member: str, *, publish: Any
    ) -> dict[str, Any]:
        self.calls.append(("charge_member", {"circle_id": circle_id, "round_n": round_n, "member": member}))
        if self.charge_error is not None:
            raise self.charge_error
        status = "DECLINED" if member in self.declines else "COMPLETED"
        row = self._row(round_n, member, status)
        self.contributions[(round_n, member)] = row
        await publish({"type": "chip", "round_n": round_n, "member": member, "status": status})
        return {k: row[k] for k in ("member", "status", "order_id", "capture_id", "decline_code", "label")}

    async def confirm_capture(
        self, circle_id: str, round_n: int, member: str, *, publish: Any
    ) -> dict[str, Any]:
        self.calls.append(("confirm_capture", {"circle_id": circle_id, "round_n": round_n, "member": member}))
        row = self.contributions.get((round_n, member))
        if not row or row["status"] != "COMPLETED":
            raise KeyError(f"no completed contribution for {member} in round {round_n}")
        return {
            "ok": True,
            "order_id": row["order_id"],
            "capture_id": row["capture_id"],
            "status": "COMPLETED",
            "gross": "200.00",
            "fee": "7.47",
            "net": "192.53",
            "source": "toolkit:get_order_details",
        }

    async def available_balance(self, circle_id: str) -> dict[str, Any]:
        self.calls.append(("available_balance", {"circle_id": circle_id}))
        return {
            "paypal_available": self.paypal_available,
            "circle_available": "1405.18",
            "source": "balances" if self.paypal_available is not None else "ledger",
        }

    async def pay_round(self, circle_id: str, round_n: int, *, publish: Any) -> dict[str, Any]:
        self.calls.append(("pay_round", {"circle_id": circle_id, "round_n": round_n}))
        if self.pay_error is not None:
            raise self.pay_error
        for member in MEMBERS:
            row = self.contributions.get((round_n, member))
            if not row or row["status"] not in ("COMPLETED", "COVERED"):
                raise PolicyDenied(
                    f"payout: {member} is {row['status'] if row else 'PENDING'} in round {round_n}"
                )
        payout = {
            "batch_id": f"BATCH-{round_n}",
            "item_id": f"ITEM-{round_n}",
            "status": "SUCCESS",
            "amount": Decimal("1155.18"),
            "recipient": ROTATION[round_n - 1],
            "wallet": "PAYPAL",
        }
        self.payouts[round_n] = payout
        await publish({"type": "payout", "round_n": round_n, "status": "SUCCESS"})
        return payout

    async def payout_status(self, circle_id: str, round_n: int) -> dict[str, Any]:
        self.calls.append(("payout_status", {"circle_id": circle_id, "round_n": round_n}))
        payout = self.payouts.get(round_n)
        if payout is None:
            return {"status": None, "item_id": None, "batch_id": None}
        return {"status": payout["status"], "item_id": payout["item_id"], "batch_id": payout["batch_id"]}

    # -- app.engine.state ------------------------------------------------------------------------------------
    def charter(self) -> FakeCharter:
        return FakeCharter(
            members=[
                FakeMember(
                    handle=m,
                    name=NAMES[m],
                    lang="hi" if m == "ravi" else "en",
                    email=f"{m}@example.com",
                    payday_rule="every 2nd Friday" if m == "ravi" else None,
                    payout_receiver=f"{m}-payee@example.com",
                    track_record_rounds=2 if m in ("meena", "asha") else 0,
                )
                for m in MEMBERS
            ]
        )

    def load_charter(self, circle_id: str) -> FakeCharter:
        self.calls.append(("load_charter", {"circle_id": circle_id}))
        return self.charter()

    def is_fixture_clone(self, circle_id: str) -> bool:
        return circle_id in self.demo_circles

    def board_model(self, circle_id: str) -> dict[str, Any]:
        self.calls.append(("board_model", {"circle_id": circle_id}))
        rounds = []
        for n in range(1, 7):
            payout = self.payouts.get(n)
            rounds.append(
                {
                    "n": n,
                    "due_at": f"2026-{10 + n:02d}-01T00:00:00+00:00"
                    if n <= 2
                    else f"2027-{n - 2:02d}-01T00:00:00+00:00",
                    "recipient": ROTATION[n - 1],
                    "status": "PAID" if (n <= 2 or payout) else "PENDING",
                    "payout": {
                        "batch_id": payout["batch_id"],
                        "item_id": payout["item_id"],
                        "status": payout["status"],
                        "amount": str(payout["amount"]),
                    }
                    if payout
                    else None,
                    "paid_at": None,
                }
            )
        return {
            "circle": {
                "id": circle_id,
                "name": "Temple Circle A",
                "amount": Decimal("200.00"),
                "currency": "USD",
                "cadence": "monthly",
                "charter_sha256": "f" * 64,
                "fee_policy": "net_pot",
                "buffer_balance": Decimal("250.00"),
                "circle_available": Decimal("1405.18"),
                "status": "active",
                "virtual_now": dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
                "run_id": "abcdefgh",
                "rotation": ROTATION,
                "mandate_present": True,
                "is_fixture_clone": circle_id in self.demo_circles,
                "flags": {"ravi_declines": "ravi" in self.declines, "next_payout_unclaimed": False},
            },
            "members": [
                {
                    "handle": m,
                    "name": NAMES[m],
                    "lang": "hi" if m == "ravi" else "en",
                    "payout_wallet": "PAYPAL",
                    "vaulted": True,
                    "rotation_pos": ROTATION.index(m) + 1,
                    "email": f"{m}@example.com",
                    "sandbox_account": f"sb-{m}@business.example.com",
                }
                for m in MEMBERS
            ],
            "rounds": rounds,
            "contributions": [dict(row) for row in self.contributions.values()],
            "counters": {"organizer_messages_typed": 0, "rounds_paid_on_time": 2, "shortfalls_covered": 0},
            "ticker": {"last_capture_at": None, "paid_at": None, "seconds": None},
        }

    # -- installation ----------------------------------------------------------------------------------------
    def install(self, monkeypatch: Any) -> FakeEngine:
        rounds = types.ModuleType("app.engine.rounds")
        for name in (
            "collect_round",
            "charge_member",
            "confirm_capture",
            "available_balance",
            "pay_round",
            "payout_status",
        ):
            setattr(rounds, name, getattr(self, name))
        state = types.ModuleType("app.engine.state")
        for name in ("load_charter", "board_model", "is_fixture_clone"):
            setattr(state, name, getattr(self, name))
        policy = types.ModuleType("app.domain.policy")
        policy.PolicyDenied = PolicyDenied
        client = types.ModuleType("app.paypal.client")
        client.PayPalError = PayPalError
        monkeypatch.setitem(sys.modules, "app.engine.rounds", rounds)
        monkeypatch.setitem(sys.modules, "app.engine.state", state)
        monkeypatch.setitem(sys.modules, "app.domain.policy", policy)
        monkeypatch.setitem(sys.modules, "app.paypal.client", client)
        return self
