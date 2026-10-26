"""Gateway: strict FIFO, spacing, rolling window, position(), record callback, run_sync, lifecycle, singleton."""

from __future__ import annotations

import asyncio
import threading
import time
from itertools import pairwise

import httpx
import pytest

from app.paypal.client import PayPalError
from app.paypal.gateway import Gateway, get_gateway, set_gateway


async def test_three_concurrent_calls_run_fifo_and_spaced(client, paypal_mock):
    gap_s = 0.2  # rate 300/min -> min(0.3, 60/300) = 200 ms; generous enough for Windows timer granularity
    gw = Gateway(client, rate_per_min=300)
    assert gw.min_gap_s == pytest.approx(gap_s)
    order: list[str] = []
    started: list[float] = []
    completed: list[tuple[str, float]] = []

    def handler(request, name):
        order.append(name)
        started.append(time.perf_counter())
        return httpx.Response(200, json={"name": name})

    async def call(name: str):
        resp = await gw.call("GET", f"/t/{name}")
        completed.append((name, time.perf_counter()))
        return resp

    paypal_mock.get(path__regex=r"/t/(?P<name>[abc])$").mock(side_effect=handler)
    try:
        results = await asyncio.wait_for(asyncio.gather(call("a"), call("b"), call("c")), timeout=5.0)
    finally:
        await gw.stop()
    assert order == ["a", "b", "c"]
    assert [name for name, _ in completed] == ["a", "b", "c"]
    assert [r.data["name"] for r in results] == ["a", "b", "c"]
    completion_ts = [ts for _, ts in completed]
    assert completion_ts == sorted(completion_ts)
    gaps = [b - a for a, b in pairwise(started)]
    assert len(gaps) == 2
    assert all(gap >= gap_s - 0.05 for gap in gaps), gaps  # never tighter than 50 ms on wall-clock timing
    assert gw.stats["calls"] == 3 and gw.stats["errors"] == 0


async def test_spacing_is_exactly_min_gap_with_a_fake_clock(client, paypal_mock):
    """Deterministic twin of the test above: the worker sleeps exactly min_gap between consecutive calls."""
    gw = Gateway(client, rate_per_min=300)
    now = [1000.0]
    sleeps: list[float] = []
    gw._clock = lambda: now[0]

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    gw._sleep = fake_sleep
    order: list[str] = []
    paypal_mock.get(path__regex=r"/t/(?P<name>[abc])$").mock(
        side_effect=lambda request, name: (order.append(name), httpx.Response(200, json={"name": name}))[1]
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(gw.call("GET", "/t/a"), gw.call("GET", "/t/b"), gw.call("GET", "/t/c")),
            timeout=5.0,
        )
    finally:
        await gw.stop()
    assert order == ["a", "b", "c"]
    assert sleeps == [pytest.approx(0.2), pytest.approx(0.2)]
    assert gw.stats["throttled_s"] == pytest.approx(0.4)


async def test_rolling_window_blocks_the_call_over_budget(client, paypal_mock):
    gw = Gateway(client, rate_per_min=2, min_gap_s=0.0)
    now = [1000.0]
    sleeps: list[float] = []
    gw._clock = lambda: now[0]

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    gw._sleep = fake_sleep
    paypal_mock.get("/t/x").mock(return_value=httpx.Response(200, json={}))
    try:
        for _ in range(3):
            await gw.call("GET", "/t/x")
        assert sleeps == [pytest.approx(60.0)]
        assert gw.stats["throttled_s"] == pytest.approx(60.0)
        await gw.call("GET", "/t/x")  # one more inside the new window: no extra wait
        assert len(sleeps) == 1
    finally:
        await gw.stop()


async def test_min_gap_default_is_300ms_at_40_per_min(client):
    gw = Gateway(client)
    assert gw.rate_per_min == 40
    assert gw.min_gap_s == pytest.approx(0.3)
    with pytest.raises(ValueError):
        Gateway(client, rate_per_min=0)


async def test_position_counts_inflight_and_queued(gateway, paypal_mock):
    release = threading.Event()
    paypal_mock.get("/t/q").mock(return_value=httpx.Response(200, json={}))
    assert gateway.position() == 0
    blocker = asyncio.ensure_future(gateway.run_sync(lambda: release.wait(5)))
    await asyncio.sleep(0.05)  # worker picks the blocking job up
    second = asyncio.ensure_future(gateway.call("GET", "/t/q"))
    third = asyncio.ensure_future(gateway.call("GET", "/t/q"))
    await asyncio.sleep(0)
    assert gateway.position() == 3
    release.set()
    assert await blocker is True
    await asyncio.gather(second, third)
    assert gateway.position() == 0


async def test_record_callback_receives_every_call(client, paypal_mock):
    records: list[dict] = []
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0, record=records.append)
    paypal_mock.post("/v2/checkout/orders").mock(
        side_effect=[
            httpx.Response(201, json={"id": "O1"}),
            httpx.Response(
                422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED"}]}
            ),
        ]
    )
    try:
        ok = await gw.call("POST", "/v2/checkout/orders", json={}, request_id="tk:a:b:1:m:a1")
        assert ok.data == {"id": "O1"}
        with pytest.raises(PayPalError) as exc:
            await gw.call("POST", "/v2/checkout/orders", json={}, request_id="tk:a:b:1:m:a2")
        assert exc.value.issue_codes == ["INSTRUMENT_DECLINED"]
    finally:
        await gw.stop()
    assert len(records) == 2
    assert set(records[0]) == {"ts", "method", "path", "status", "request_id", "ms"}
    assert records[0]["method"] == "POST" and records[0]["path"] == "/v2/checkout/orders"
    assert records[0]["status"] == 201 and records[0]["request_id"] == "tk:a:b:1:m:a1"
    assert records[1]["status"] == 422 and records[1]["request_id"] == "tk:a:b:1:m:a2"
    assert records[0]["ts"].endswith("+00:00") and isinstance(records[0]["ms"], int)
    assert gw.stats["errors"] == 1


async def test_failing_record_callback_does_not_break_calls(client, paypal_mock):
    def bad_record(rec: dict) -> None:
        raise RuntimeError("db down")

    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0, record=bad_record)
    paypal_mock.get("/t/r").mock(return_value=httpx.Response(200, json={"ok": True}))
    try:
        assert (await gw.call("GET", "/t/r")).data == {"ok": True}
    finally:
        await gw.stop()


async def test_run_sync_runs_in_a_thread_in_queue_order(client, paypal_mock):
    records: list[dict] = []
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0, record=records.append)
    seen: list[str] = []
    paypal_mock.get(path__regex=r"/t/(?P<name>[AC])$").mock(
        side_effect=lambda request, name: (seen.append(name), httpx.Response(200, json={}))[1]
    )

    def blocking() -> bool:
        seen.append("B")
        return threading.current_thread() is not threading.main_thread()

    try:
        a, b, c = await asyncio.gather(gw.call("GET", "/t/A"), gw.run_sync(blocking), gw.call("GET", "/t/C"))
    finally:
        await gw.stop()
    assert seen == ["A", "B", "C"]
    assert b is True
    assert (
        records[1]["method"] == "TOOLKIT" and records[1]["path"] == "blocking" and records[1]["status"] == 200
    )


async def test_run_sync_propagates_exceptions_and_records_status(client):
    records: list[dict] = []
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0, record=records.append)

    def boom() -> None:
        raise PayPalError(404, {"name": "RESOURCE_NOT_FOUND"}, method="TOOLKIT", path="get_order_details")

    def plain() -> None:
        raise ValueError("nope")

    try:
        with pytest.raises(PayPalError):
            await gw.run_sync(boom, path="get_order_details")
        with pytest.raises(ValueError):
            await gw.run_sync(plain)
    finally:
        await gw.stop()
    assert [r["status"] for r in records] == [404, 0]
    assert records[0]["path"] == "get_order_details"


async def test_stop_fails_pending_jobs_and_calls_restart_the_worker(client, paypal_mock):
    gw = Gateway(client, rate_per_min=600, min_gap_s=5.0)
    paypal_mock.get("/t/s").mock(return_value=httpx.Response(200, json={"n": 1}))
    await gw.call("GET", "/t/s")
    pending = asyncio.ensure_future(gw.call("GET", "/t/s"))  # throttled for 5 s
    await asyncio.sleep(0.05)
    assert gw.running
    await gw.stop()
    assert not gw.running
    with pytest.raises(RuntimeError):
        await pending
    gw.min_gap_s = 0.0
    assert (await gw.call("GET", "/t/s")).data == {"n": 1}  # lazily restarted
    assert gw.running
    await gw.stop()


async def test_cancelled_caller_does_not_break_the_worker(client, paypal_mock):
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.2)
    paypal_mock.get("/t/c").mock(return_value=httpx.Response(200, json={}))
    try:
        await gw.call("GET", "/t/c")
        task = asyncio.ensure_future(gw.call("GET", "/t/c"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gw.min_gap_s = 0.0
        assert (await gw.call("GET", "/t/c")).status == 200
    finally:
        await gw.stop()


async def test_max_inflight_overlaps_calls_but_starts_them_in_fifo_order(client):
    """R4: with max_inflight=3, three blocking toolkit-style jobs are in flight together (a 3-party barrier only
    opens if they overlap) and still start in submission order."""
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0, max_inflight=3)
    barrier = threading.Barrier(3, timeout=5)
    started: list[str] = []

    def job(name: str):
        def _run() -> str:
            started.append(name)
            barrier.wait()  # BrokenBarrierError after 5 s if the jobs ran one at a time
            return name

        _run.__name__ = name
        return _run

    try:
        out = await asyncio.wait_for(
            asyncio.gather(gw.run_sync(job("a")), gw.run_sync(job("b")), gw.run_sync(job("c"))), timeout=10
        )
    finally:
        await gw.stop()
    assert out == ["a", "b", "c"]
    assert started == ["a", "b", "c"]


async def test_max_inflight_caps_concurrency(client):
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0, max_inflight=2)
    lock = threading.Lock()
    live = [0]
    peak = [0]

    def slow() -> None:
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.15)
        with lock:
            live[0] -= 1

    try:
        await asyncio.wait_for(asyncio.gather(*(gw.run_sync(slow) for _ in range(5))), timeout=10)
    finally:
        await gw.stop()
    assert peak[0] == 2
    assert gw.stats["calls"] == 5


async def test_max_inflight_keeps_spacing_and_the_rolling_window(client, paypal_mock):
    """Concurrency never loosens the budget: starts stay min_gap apart and the 60 s window still blocks."""
    gw = Gateway(client, rate_per_min=3, min_gap_s=0.2, max_inflight=4)
    now = [1000.0]
    sleeps: list[float] = []
    gw._clock = lambda: now[0]

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    gw._sleep = fake_sleep
    paypal_mock.get("/t/w").mock(return_value=httpx.Response(200, json={}))
    try:
        await asyncio.wait_for(asyncio.gather(*(gw.call("GET", "/t/w") for _ in range(4))), timeout=5)
    finally:
        await gw.stop()
    # calls 2 and 3 wait the 200 ms gap; call 4 waits for the first start to leave the 60 s window
    assert sleeps[:2] == [pytest.approx(0.2), pytest.approx(0.2)]
    assert sum(sleeps) == pytest.approx(60.0)
    assert gw.stats["calls"] == 4


async def test_stop_fails_inflight_jobs(client):
    gw = Gateway(client, rate_per_min=600, min_gap_s=0.0, max_inflight=2)
    release = threading.Event()
    job = asyncio.ensure_future(gw.run_sync(lambda: release.wait(5)))
    await asyncio.sleep(0.05)
    assert gw.position() == 1
    await gw.stop()
    release.set()
    with pytest.raises(RuntimeError):
        await job
    assert gw.position() == 0


async def test_max_inflight_must_be_positive(client):
    with pytest.raises(ValueError):
        Gateway(client, max_inflight=0)


async def test_get_gateway_is_a_lazy_singleton(settings):
    from app.paypal.gateway import LIVE_MAX_INFLIGHT

    set_gateway(None)
    try:
        g1 = get_gateway()
        g2 = get_gateway()
        assert g1 is g2
        assert g1.max_inflight == LIVE_MAX_INFLIGHT > 1
        assert g1.rate_per_min == settings.paypal_rate_per_min
        assert g1.client.base_url == "https://api-m.sandbox.paypal.com"
        assert not g1.running  # nothing started until the first call
    finally:
        set_gateway(None)
