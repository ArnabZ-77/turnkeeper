"""Shared factories for the domain tests. Everything here is fictional: no real IDs, accounts or secrets."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from app.domain.charter import Charter, charter_sha256
from app.domain.policy import CircleState

DEMO_MEMBERS: list[dict[str, Any]] = [
    {"handle": "asha", "name": "Asha Rao", "email": "asha@example.com", "track_record_rounds": 2},
    {"handle": "ravi", "name": "Ravi Kumar", "lang": "hi", "payday_rule": "every 2nd Friday"},
    {"handle": "meena", "name": "Meena Iyer", "track_record_rounds": 4},
    {"handle": "tomas", "name": "Tomas Lee"},
    {"handle": "lin", "name": "Lin Zhao", "payout_wallet": "VENMO", "payout_receiver": "+15555550123"},
    {"handle": "dev", "name": "Dev Patel"},
]
# meena and asha have a track record, so they go first (collateral rule); round 3 pays dev.
DEMO_ROTATION = ["meena", "asha", "dev", "lin", "tomas", "ravi"]


def _charter_dict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Temple Circle A",
        "amount": "200.00",
        "cadence": "monthly",
        "start_date": "2026-11-01",
        "members": deepcopy(DEMO_MEMBERS),
        "rotation": list(DEMO_ROTATION),
        "buffer": {"seed": "250.00"},
        "quorum": "4/6",
    }
    data.update(overrides)
    return data


@pytest.fixture
def charter_dict() -> Callable[..., dict[str, Any]]:
    return _charter_dict


@pytest.fixture
def make_charter() -> Callable[..., Charter]:
    def _make(**overrides: Any) -> Charter:
        return Charter.model_validate(_charter_dict(**overrides))

    return _make


@pytest.fixture
def charter(make_charter: Callable[..., Charter]) -> Charter:
    return make_charter()


@pytest.fixture
def completed(charter: Charter) -> dict[str, str]:
    return {m.handle: "COMPLETED" for m in charter.members}


@pytest.fixture
def make_state(charter: Charter) -> Callable[..., CircleState]:
    """A round-3 state where everyone paid and a payout is possible; override anything."""

    def _make(for_charter: Charter | None = None, **overrides: Any) -> CircleState:
        c = for_charter or charter
        handles = [m.handle for m in c.members]
        data: dict[str, Any] = {
            "circle_id": "abc123",
            "run_id": "abcdefgh",
            "round_n": 3,
            "charter_sha256": charter_sha256(c),
            "contributions": {h: "COMPLETED" for h in handles},
            "attempts": {h: 1 for h in handles},
            "buffer_balance": "250.00",
            "circle_available": "1155.18",  # 6 x 192.53
            "paypal_available": "5000.00",
            "mandate_ok": True,
        }
        data.update(overrides)
        return CircleState.model_validate(data)

    return _make
