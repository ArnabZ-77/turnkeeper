"""Fakes for the UNCLAIMED re-route tests (D1): per-item payout statuses, a batch-status script, item cancel.

Fictional ids only; nothing reaches the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fakes import FakePayPal

from app.engine import rounds
from app.paypal import payouts as real_payouts
from app.paypal.client import PayPalError


class FakeClock:
    """Injectable sleep + monotonic clock: sleeping advances the clock, nothing waits for real."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s

    def __call__(self) -> float:
        return self.now


class RerouteFake(FakePayPal):
    """FakePayPal with per-item statuses, a batch-status script and the item cancel endpoint."""

    def __init__(
        self,
        *,
        cancel: str = "fail500",
        batch_script: tuple[str, ...] = ("PROCESSING", "PROCESSING", "SUCCESS"),
        first_status: str = "UNCLAIMED",
        reroute_status: str = "SUCCESS",
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.cancel_mode = cancel
        self.batch_script = batch_script
        self.first_status = first_status
        self.reroute_status = reroute_status
        self.status_by_item: dict[str, str] = {}
        self.batch_polls: dict[str, int] = {}
        self.cancel_calls: list[str] = []

    async def create_payout(self, gw, *, sender_batch_id, items, email_subject, **kw) -> dict:
        out = await super().create_payout(
            gw, sender_batch_id=sender_batch_id, items=items, email_subject=email_subject
        )
        item_id = self.payouts[-1]["item_id"]
        self.status_by_item[item_id] = self.first_status if len(self.payouts) == 1 else self.reroute_status
        return out

    def _item(self, batch_id=None, item_id=None) -> dict:
        item = super()._item(batch_id=batch_id, item_id=item_id)
        item["transaction_status"] = self.status_by_item.get(item["payout_item_id"], self.payout_status)
        return item

    async def get_payout_batch(self, gw, *, batch_id: str) -> dict:
        n = self.batch_polls.get(batch_id, 0)
        self.batch_polls[batch_id] = n + 1
        is_first = self.payouts and self.payouts[0]["batch_id"] == batch_id
        status = self.batch_script[min(n, len(self.batch_script) - 1)] if is_first else "SUCCESS"
        return {
            "batch_header": {"payout_batch_id": batch_id, "batch_status": status},
            "items": [self._item(batch_id=batch_id)],
        }

    async def cancel_payout_item(self, gw, *, item_id: str) -> dict:
        self.cancel_calls.append(item_id)
        if self.cancel_mode == "fail400":
            raise PayPalError(400, {"name": "BATCH_NOT_COMPLETED", "message": "Batch is not completed yet"})
        if self.cancel_mode == "fail500":
            raise PayPalError(500, {"name": "INTERNAL_SERVICE_ERROR", "message": "Internal server error"})
        if self.cancel_mode == "empty":  # 200/204 with no body: nothing confirms the cancel
            return {}
        self.status_by_item[item_id] = "RETURNED"
        return {"payout_item_id": item_id, "transaction_status": "RETURNED"}

    def install(self, monkeypatch) -> RerouteFake:
        super().install(monkeypatch)
        payouts_mod = SimpleNamespace(
            PayoutItem=real_payouts.PayoutItem,
            create_payout=self.create_payout,
            get_payout_batch=self.get_payout_batch,
            get_payout_item=self.get_payout_item,
            cancel_payout_item=self.cancel_payout_item,
            first_item=real_payouts.first_item,
            item_status=real_payouts.item_status,
        )
        monkeypatch.setattr(rounds, "_payouts", lambda: payouts_mod)
        return self
