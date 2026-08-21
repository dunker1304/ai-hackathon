"""Token-bucket / semaphore rate limiter, per-host and global."""

from __future__ import annotations

import asyncio
import random
import time

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TokenBucket:
    """Async token bucket. `rate` tokens are refilled per second, capped at `burst`."""

    def __init__(self, rate: float, burst: int | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = rate
        self.burst = burst if burst is not None else max(1, int(rate))
        self._tokens = float(self.burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self.rate
            await asyncio.sleep(wait)


class RateLimiter:
    """Global concurrency cap + per-host token bucket + optional jitter delay.

    Usage:
        limiter = RateLimiter(rate=1.0, burst=3, max_concurrency=4)
        async with limiter.slot("https://www.amazon.com/s?k=mug"):
            ...
    """

    def __init__(
        self,
        *,
        rate: float = 1.0,
        burst: int | None = None,
        max_concurrency: int = 4,
        per_host_concurrency: int | None = None,
        jitter: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self.rate = rate
        self.burst = burst
        self.jitter = jitter
        self._global_sem = asyncio.Semaphore(max_concurrency)
        self._per_host_concurrency = per_host_concurrency
        self._buckets: dict[str, TokenBucket] = {}
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._cooldowns: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _host_of(url: str) -> str:
        return urlparse(url).netloc or "_default"

    async def _bucket(self, host: str) -> TokenBucket:
        async with self._lock:
            bucket = self._buckets.get(host)
            if bucket is None:
                bucket = TokenBucket(self.rate, self.burst)
                self._buckets[host] = bucket
            return bucket

    async def _host_sem(self, host: str) -> asyncio.Semaphore | None:
        if self._per_host_concurrency is None:
            return None
        async with self._lock:
            sem = self._host_sems.get(host)
            if sem is None:
                sem = asyncio.Semaphore(self._per_host_concurrency)
                self._host_sems[host] = sem
            return sem

    async def _await_cooldown(self, host: str) -> None:
        while True:
            until = self._cooldowns.get(host)
            if until is None:
                return
            remaining = until - time.monotonic()
            if remaining <= 0:
                self._cooldowns.pop(host, None)
                return
            await asyncio.sleep(remaining)

    # -- public API --------------------------------------------------------

    def penalize(self, url: str, seconds: float) -> None:
        """Freeze a host after a 429 / block for `seconds`."""
        host = self._host_of(url)
        until = time.monotonic() + seconds
        self._cooldowns[host] = max(self._cooldowns.get(host, 0.0), until)

    @asynccontextmanager
    async def slot(self, url: str, *, cost: float = 1.0) -> AsyncIterator[None]:
        host = self._host_of(url)
        host_sem = await self._host_sem(host)
        bucket = await self._bucket(host)

        async with self._global_sem:
            if host_sem is not None:
                await host_sem.acquire()
            try:
                await self._await_cooldown(host)
                await bucket.acquire(cost)
                lo, hi = self.jitter
                if hi > 0:
                    await asyncio.sleep(random.uniform(lo, hi))
                yield
            finally:
                if host_sem is not None:
                    host_sem.release()
