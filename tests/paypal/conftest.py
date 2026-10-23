"""Shared fixtures for the PayPal layer: respx-mocked sandbox, a client that never sleeps, a fast gateway."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.paypal.client import PayPalClient
from app.paypal.gateway import Gateway

BASE = "https://api-m.sandbox.paypal.com"
TOKEN_JSON = {"access_token": "tok-1", "token_type": "Bearer", "expires_in": 32400, "scope": "x"}


def token_response(token: str = "tok-1", expires_in: int = 32400) -> httpx.Response:
    return httpx.Response(200, json={**TOKEN_JSON, "access_token": token, "expires_in": expires_in})


@pytest.fixture
def paypal_mock():
    """respx mock of the sandbox with a default token route (name "token"). Tests add their own routes."""
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/v1/oauth2/token", name="token").mock(return_value=token_response())
        yield mock


@pytest.fixture
async def client(settings, paypal_mock):
    """A PayPalClient whose backoff sleeps are recorded in `client.sleeps` instead of waiting."""
    c = PayPalClient(settings)
    c.sleeps: list[float] = []  # type: ignore[attr-defined]

    async def fake_sleep(seconds: float) -> None:
        c.sleeps.append(seconds)  # type: ignore[attr-defined]

    c._sleep = fake_sleep
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
async def gateway(client):
    """A gateway with no spacing (600/min, 0 s gap) so wrapper tests run instantly."""
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0)
    try:
        yield gw
    finally:
        await gw.stop()
