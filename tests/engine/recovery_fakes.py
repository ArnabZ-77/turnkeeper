"""Shared fakes for the recovery-path tests (B6). Fictional data only; nothing here reaches the network.

- ``FakeInvoiceToolkit``: stands in for the PayPal Agent Toolkit's invoice tools (create_invoice ->
  send_invoice -> generate_invoice_qr_code) and records every call with its PayPal-Request-Id.
- ``use_swappable_rotation``: rewrites a cloned circle's charter to a given rotation (default: the storyboard
  order, ravi in round 5 and tomas in round 6, which the fixture itself now uses), re-hashes it, updates
  rounds/members and re-signs the demo mandate. Tests pass a ravi-last order to cover the no-swap case.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from app.db import session_scope
from app.models import Circle, MandateRow, MemberRow, Round
from app.paypal.client import PayPalError

SWAPPABLE_ROTATION = ["meena", "asha", "dev", "lin", "ravi", "tomas"]
FAKE_INVOICE_ID = "INV2-FAKE-0001"
FAKE_QR_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(120))).decode()


class FakeInvoiceToolkit:
    def __init__(self, *, fail_on: str | None = None, invoice_id: str = FAKE_INVOICE_ID) -> None:
        self.fail_on = fail_on
        self.invoice_id = invoice_id
        self.calls: list[tuple[str, dict, str | None]] = []
        self.prepared = 0

    async def prepare(self) -> None:
        self.prepared += 1

    async def run(self, method: str, params: dict, *, request_id: str | None = None) -> dict[str, Any]:
        self.calls.append((method, dict(params), request_id))
        if method == self.fail_on:
            raise PayPalError(422, {"name": "UNPROCESSABLE_ENTITY", "message": "fake invoicing failure"})
        if method == "create_invoice":
            return {
                "rel": "self",
                "href": f"https://api-m.sandbox.paypal.com/v2/invoicing/invoices/{self.invoice_id}",
                "method": "GET",
            }
        if method == "send_invoice":
            return {
                "rel": "payer-view",
                "href": f"https://www.sandbox.paypal.com/invoice/p/#{self.invoice_id}",
            }
        if method == "generate_invoice_qr_code":
            return {"result": FAKE_QR_B64}
        raise ValueError(f"tool {method} not enabled")

    def methods(self) -> list[str]:
        return [c[0] for c in self.calls]


def use_swappable_rotation(circle_id: str, rotation: list[str] | None = None) -> None:
    from app.domain.charter import Charter, canonical_json, charter_sha256
    from app.engine.fixture import _create_mandate

    order = list(rotation or SWAPPABLE_ROTATION)
    with session_scope() as db:
        circle = db.get(Circle, circle_id)
        assert circle is not None
        charter = Charter.model_validate_json(circle.charter_json)
        data = charter.model_dump(mode="json")
        data["rotation"] = order
        new = Charter.model_validate(data)
        circle.charter_json = canonical_json(new)
        circle.charter_sha256 = charter_sha256(new)
        db.add(circle)
        for rnd in db.exec(select(Round).where(Round.circle_id == circle_id)).all():
            rnd.recipient_handle = order[rnd.n - 1]
            db.add(rnd)
        for m in db.exec(select(MemberRow).where(MemberRow.circle_id == circle_id)).all():
            m.rotation_pos = order.index(m.handle) + 1
            db.add(m)
        for row in db.exec(select(MandateRow).where(MandateRow.circle_id == circle_id)).all():
            db.delete(row)
        db.flush()
        _create_mandate(db, circle, new, datetime.now(UTC))


def invoice_paid_event(
    invoice_id: str = FAKE_INVOICE_ID, *, event_id: str = "WH-FAKE-INV-1", value: str = "200.00"
) -> dict:
    return {
        "id": event_id,
        "event_type": "INVOICING.INVOICE.PAID",
        "resource": {
            "invoice": {
                "id": invoice_id,
                "status": "PAID",
                "payments": {"paid_amount": {"currency_code": "USD", "value": value}},
            }
        },
    }
