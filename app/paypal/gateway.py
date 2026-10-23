"""PayPal gateway: one FIFO worker and a rolling-window rate limit in front of `PayPalClient`.

Every PayPal call in the process (raw REST and Agent Toolkit tools alike) goes through one `Gateway`, so the
sandbox limit (~50 req/min -> 5-minute IP block) is enforced globally:

- jobs run strictly in submission order on a single asyncio worker (`call()` returns via a Future);
- at most `rate_per_min` calls start in any rolling 60-second window, and consecutive calls are at least
  `min_gap_s` apart (default `min(0.3, 60 / rate_per_min)`), so a round of ~18 calls bursts at 300 ms
  spacing while the per-minute budget still holds;
- `run_sync(fn)` runs a blocking function (the toolkit's `requests` calls) in a thread, through the same queue
  and budget;
- every call is reported to the optional `record` callback as
  `{"ts", "method", "path", "status", "request_id", "ms"}` (B4 persists these; 429s are logged too).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from app.config import get_settings
from app.paypal.client import PayPalClient, PayPalError, PayPalResponse

log = logging.getLogger("turnkeeper.paypal.gateway")

T = TypeVar("T")
WINDOW_S = 60.0
DEFAULT_MIN_GAP_S = 0.3
SYNC_METHOD = "TOOLKIT"


@dataclass
class _Job:
    future: asyncio.Future
    method: str
    path: str
    json: dict | None = None
    params: dict | None = None
    headers: dict | None = None
    request_id: str | None = None
    fn: Callable[[], Any] | None = None
    enqueued_at: float = 0.0


class Gateway:
    """Single FIFO worker + rolling-window limiter in front of PayPalClient. Shared by raw calls and the toolkit."""

    def __init__(
        self,
        client: PayPalClient,
        rate_per_min: int = 40,
        record: Callable[[dict], Any] | None = None,
        *,
        min_gap_s: float | None = None,
    ) -> None:
        if int(rate_per_min) < 1:
            raise ValueError("rate_per_min must be >= 1")
        self.client = client
        self.rate_per_min = int(rate_per_min)
        self.min_gap_s = (
            float(min_gap_s) if min_gap_s is not None else min(DEFAULT_MIN_GAP_S, WINDOW_S / rate_per_min)
        )
        self.record = record
        self._queue: asyncio.Queue[_Job] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sent: deque[float] = deque()  # start times (monotonic) of calls inside the rolling window
        self._inflight: _Job | None = None
        self.stats: dict[str, float] = {"calls": 0, "errors": 0, "rate_limited": 0, "throttled_s": 0.0}
        # Injection points for tests (fake clock / no real sleeping).
        self._sleep = asyncio.sleep
        self._clock = time.monotonic

    # ----------------------------------------------------------------------------- lifecycle

    def _ensure_started(self) -> None:
        loop = asyncio.get_running_loop()
        if self._worker is not None and not self._worker.done() and self._loop is loop:
            return
        if self._worker is not None and self._loop is not loop:
            log.debug("gateway worker belonged to another event loop; starting a new one")
        self._loop = loop
        self._queue = asyncio.Queue()
        self._inflight = None
        self._worker = loop.create_task(self._run(self._queue), name="paypal-gateway")

    async def start(self) -> None:
        self._ensure_started()

    async def stop(self) -> None:
        worker, self._worker = self._worker, None
        queue, self._queue = self._queue, None
        if worker is not None and not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await worker
        if queue is not None:
            while not queue.empty():
                job = queue.get_nowait()
                if not job.future.done():
                    job.future.set_exception(RuntimeError("PayPal gateway stopped"))
        self._inflight = None
        self._loop = None

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    # ----------------------------------------------------------------------------- public API

    def position(self) -> int:
        """Jobs ahead of a job enqueued right now (queued + in flight). Feeds the UI 'queued, position N' badge."""
        queued = self._queue.qsize() if self._queue is not None else 0
        return queued + (1 if self._inflight is not None else 0)

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        request_id: str | None = None,
    ) -> PayPalResponse:
        job = self._enqueue(
            method.upper(), path, json=json, params=params, headers=headers, request_id=request_id
        )
        return await job.future

    async def run_sync(self, fn: Callable[[], T], *, method: str = SYNC_METHOD, path: str = "") -> T:
        """Run a blocking function (e.g. a toolkit tool) in a thread, through the same FIFO queue and budget."""
        label = path or getattr(fn, "__name__", "fn")
        job = self._enqueue(method, label, fn=fn)
        return await job.future

    def _enqueue(self, method: str, path: str, **kw: Any) -> _Job:
        self._ensure_started()
        assert self._queue is not None
        loop = asyncio.get_running_loop()
        job = _Job(future=loop.create_future(), method=method, path=path, enqueued_at=self._clock(), **kw)
        self._queue.put_nowait(job)
        log.debug("queued %s %s (position %d)", method, path, self.position())
        return job

    # ----------------------------------------------------------------------------- worker

    async def _run(self, queue: asyncio.Queue[_Job]) -> None:
        while True:
            job = await queue.get()
            try:
                if job.future.done():  # the caller gave up while queued
                    continue
                await self._wait_for_slot()
                self._inflight = job
                await self._execute(job)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.set_exception(RuntimeError("PayPal gateway stopped"))
                raise
            except Exception as e:  # never let the worker die
                log.exception("gateway worker error on %s %s", job.method, job.path)
                if not job.future.done():
                    job.future.set_exception(e)
            finally:
                self._inflight = None
                queue.task_done()

    async def _wait_for_slot(self) -> None:
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0] >= WINDOW_S:
                self._sent.popleft()
            wait = 0.0
            if len(self._sent) >= self.rate_per_min:
                wait = max(wait, self._sent[0] + WINDOW_S - now)
            if self._sent:
                wait = max(wait, self._sent[-1] + self.min_gap_s - now)
            if wait <= 0.0005:
                self._sent.append(now)
                return
            self.stats["throttled_s"] += wait
            log.debug(
                "throttling %.3fs (window %d/%d per %.0fs)",
                wait,
                len(self._sent),
                self.rate_per_min,
                WINDOW_S,
            )
            await self._sleep(wait)

    async def _execute(self, job: _Job) -> None:
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        started = self._clock()
        self.stats["calls"] += 1
        result: Any
        try:
            if job.fn is not None:
                result = await asyncio.to_thread(job.fn)
                status = 200
            else:
                result = await self.client.request(
                    job.method,
                    job.path,
                    json=job.json,
                    params=job.params,
                    headers=job.headers,
                    request_id=job.request_id,
                )
                status = result.status
        except PayPalError as e:
            self.stats["errors"] += 1
            if e.status == 429:
                self.stats["rate_limited"] += 1
                log.warning(
                    "PayPal 429 RATE_LIMIT at %s on %s %s (debug_id=%s)", ts, job.method, job.path, e.debug_id
                )
            self._emit(ts, job, e.status, started)
            if not job.future.done():
                job.future.set_exception(e)
            return
        except Exception as e:
            self.stats["errors"] += 1
            self._emit(ts, job, 0, started)
            if not job.future.done():
                job.future.set_exception(e)
            return
        self._emit(ts, job, status, started)
        if not job.future.done():
            job.future.set_result(result)

    def _emit(self, ts: str, job: _Job, status: int, started: float) -> None:
        if self.record is None:
            return
        rec = {
            "ts": ts,
            "method": job.method,
            "path": job.path,
            "status": int(status),
            "request_id": job.request_id,
            "ms": int((self._clock() - started) * 1000),
        }
        try:
            out = self.record(rec)
            if inspect.isawaitable(out):
                asyncio.ensure_future(out)
        except Exception:
            log.exception("gateway record callback failed")


# --------------------------------------------------------------------------------- process-wide singleton

_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """Lazy process-wide gateway built from settings (rate = TK_PAYPAL_RATE_PER_MIN). Used by engine/mcp."""
    global _gateway
    if _gateway is None:
        settings = get_settings()
        _gateway = Gateway(PayPalClient(settings), rate_per_min=settings.paypal_rate_per_min)
    return _gateway


def set_gateway(gw: Gateway | None) -> None:
    """Replace (or clear) the singleton: app wiring installs the `record` callback, tests inject a mocked one."""
    global _gateway
    _gateway = gw
