"""In-memory stand-ins for the PayPal accessors of app.engine.rounds (no network, no respx needed).

`FakePayPal().install(monkeypatch)` replaces rounds._gateway/_orders/_payouts/_balances/_toolkit with
objects that behave like B1's wrappers: a mocked decline raises a real PayPalError carrying
INSTRUMENT_DECLINED, successful charges return COMPLETED orders with a seller_receivable_breakdown, payouts
answer with a batch + one item, and the toolkit returns the stored order for get_order_details.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlmodel import select

from app.db import session_scope
from app.engine.common import money
from app.models import MemberRow
from app.paypal import balances as real_balances
from app.paypal import orders as real_orders
from app.paypal import payouts as real_payouts
from app.paypal.client import PayPalError

DECLINE_RAW = {
    "name": "UNPROCESSABLE_ENTITY",
    "message": "The requested action could not be performed, semantically incorrect, or failed business validation.",
    "debug_id": "fake-debug-id",
    "details": [
        {
            "issue": "INSTRUMENT_DECLINED",
            "description": "The instrument presented was either declined by the processor or bank.",
        }
    ],
}


class FakeGateway:
    def __init__(self) -> None:
        self.client = SimpleNamespace(name="fake-client")
        self.queued = 0

    def position(self) -> int:
        return self.queued


class FakeToolkit:
    def __init__(self, orders: dict[str, dict]) -> None:
        self._orders = orders
        self.prepared = 0
        self.calls: list[tuple[str, dict]] = []

    async def prepare(self) -> None:
        self.prepared += 1

    async def run(self, method: str, params: dict, *, request_id: str | None = None) -> dict:
        self.calls.append((method, dict(params)))
        if method != "get_order_details":
            raise ValueError(f"tool {method} not enabled")
        order = self._orders.get(params["order_id"])
        if order is None:
            raise PayPalError(404, {"name": "RESOURCE_NOT_FOUND", "message": "order not found"})
        return dict(order)


class FakePayPal:
    """Stateful fake of the PayPal side used by app.engine.rounds."""

    def __init__(
        self,
        *,
        decline_members: tuple[str, ...] = (),
        fee: str = "7.47",
        balance: str | None = "5000.00",
        balances_error: bool = False,
        payout_status: str = "SUCCESS",
        payout_fee: str = "0.25",
        payout_error: bool = False,
    ) -> None:
        self.decline_members = set(decline_members)
        self.fee = money(fee)
        self.balance = balance
        self.balances_error = balances_error
        self.payout_status = payout_status
        self.payout_fee = payout_fee
        self.payout_error = payout_error
        self.gateway = FakeGateway()
        self.charges: list[dict[str, Any]] = []
        self.orders: dict[str, dict] = {}
        self.payouts: list[dict[str, Any]] = []
        self.item_polls: list[str] = []
        self.toolkit = FakeToolkit(self.orders)

    # --- orders ---------------------------------------------------------------------------------

    async def charge_vaulted(
        self,
        gw: Any,
        *,
        request_id: str,
        vault_id: str,
        amount: Decimal,
        currency: str = "USD",
        reference_id: str,
        custom_id: str,
        description: str,
        mock_decline: bool = False,
    ) -> dict:
        call = {
            "request_id": request_id,
            "vault_id": vault_id,
            "amount": money(amount),
            "currency": currency,
            "reference_id": reference_id,
            "custom_id": custom_id,
            "description": description,
            "mock_decline": mock_decline,
        }
        self.charges.append(call)
        member = reference_id.split(":")[-1]
        if mock_decline or member in self.decline_members:
            raise PayPalError(422, DECLINE_RAW, method="POST", path="/v2/checkout/orders")
        n = len(self.charges)
        gross = money(amount)
        order_id = f"FAKEORD{n:04d}"
        capture_id = f"FAKECAP{n:04d}"
        order = {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "payments": {
                        "captures": [
                            {
                                "id": capture_id,
                                "status": "COMPLETED",
                                "amount": {"currency_code": currency, "value": str(gross)},
                                "seller_receivable_breakdown": {
                                    "gross_amount": {"currency_code": currency, "value": str(gross)},
                                    "paypal_fee": {"currency_code": currency, "value": str(self.fee)},
                                    "net_amount": {
                                        "currency_code": currency,
                                        "value": str(money(gross - self.fee)),
                                    },
                                },
                            }
                        ]
                    },
                }
            ],
        }
        self.orders[order_id] = order
        return order

    async def get_order(self, gw: Any, *, order_id: str) -> dict:
        return dict(self.orders[order_id])

    # --- payouts --------------------------------------------------------------------------------

    async def create_payout(
        self, gw: Any, *, sender_batch_id: str, items: list, email_subject: str, **kw
    ) -> dict:
        if self.payout_error:
            raise PayPalError(
                422,
                {"name": "INSUFFICIENT_FUNDS", "message": "Sender does not have sufficient funds."},
            )
        n = len(self.payouts) + 1
        batch_id = f"FAKEBATCH{n:03d}"
        item_id = f"FAKEITEM{n:03d}"
        self.payouts.append(
            {
                "sender_batch_id": sender_batch_id,
                "batch_id": batch_id,
                "item_id": item_id,
                "items": [i.model_dump() for i in items],
                "email_subject": email_subject,
            }
        )
        return {"batch_header": {"payout_batch_id": batch_id, "batch_status": "PENDING"}}

    def _item(self, batch_id: str | None = None, item_id: str | None = None) -> dict:
        for p in self.payouts:
            if p["batch_id"] == batch_id or p["item_id"] == item_id:
                return {
                    "payout_item_id": p["item_id"],
                    "transaction_status": self.payout_status,
                    "payout_item_fee": {"currency": "USD", "value": self.payout_fee},
                    "payout_item": p["items"][0],
                }
        raise PayPalError(404, {"name": "RESOURCE_NOT_FOUND", "message": "payout not found"})

    async def get_payout_batch(self, gw: Any, *, batch_id: str) -> dict:
        return {
            "batch_header": {"payout_batch_id": batch_id, "batch_status": "SUCCESS"},
            "items": [self._item(batch_id=batch_id)],
        }

    async def get_payout_item(self, gw: Any, *, item_id: str) -> dict:
        self.item_polls.append(item_id)
        return self._item(item_id=item_id)

    # --- balances -------------------------------------------------------------------------------

    async def get_balances(self, gw: Any, *, currency: str = "USD", as_of_time: str | None = None) -> dict:
        if self.balances_error:
            raise PayPalError(403, {"name": "NOT_AUTHORIZED", "message": "balances scope missing"})
        if self.balance is None:
            return {"balances": []}
        return {
            "balances": [
                {
                    "currency": currency,
                    "primary": True,
                    "total_balance": {"currency_code": currency, "value": self.balance},
                    "available_balance": {"currency_code": currency, "value": self.balance},
                }
            ]
        }

    # --- install --------------------------------------------------------------------------------

    def install(self, monkeypatch) -> FakePayPal:
        from app.engine import rounds

        orders_mod = SimpleNamespace(
            charge_vaulted=self.charge_vaulted,
            get_order=self.get_order,
            capture_summary=real_orders.capture_summary,
        )
        payouts_mod = SimpleNamespace(
            PayoutItem=real_payouts.PayoutItem,
            create_payout=self.create_payout,
            get_payout_batch=self.get_payout_batch,
            get_payout_item=self.get_payout_item,
            first_item=real_payouts.first_item,
            item_status=real_payouts.item_status,
        )
        balances_mod = SimpleNamespace(
            get_balances=self.get_balances, available_amount=real_balances.available_amount
        )
        monkeypatch.setattr(rounds, "_gateway", lambda: self.gateway)
        monkeypatch.setattr(rounds, "_orders", lambda: orders_mod)
        monkeypatch.setattr(rounds, "_payouts", lambda: payouts_mod)
        monkeypatch.setattr(rounds, "_balances", lambda: balances_mod)
        monkeypatch.setattr(rounds, "_toolkit", lambda: self.toolkit)
        return self


def vault_members(circle_id: str, *, receiver: bool = True) -> None:
    """Give every member a fake vault id (and a payout receiver) so charges and payouts are allowed."""
    with session_scope() as db:
        for m in db.exec(select(MemberRow).where(MemberRow.circle_id == circle_id)).all():
            m.vault_id = f"FAKEVAULT{m.handle.upper()}"
            m.customer_id = f"FAKECUST{m.handle.upper()}"
            if receiver:
                m.payout_receiver = f"{m.handle}@example.com"
            db.add(m)


class Collector:
    """An async publish() that records events, with small query helpers."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event: dict) -> None:
        self.events.append(dict(event))

    def of_type(self, type_: str) -> list[dict]:
        return [e for e in self.events if e.get("type") == type_]

    def chips(self, member: str | None = None) -> list[dict]:
        return [e for e in self.of_type("chip") if member is None or e.get("member") == member]
