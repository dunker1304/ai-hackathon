"""Retry policy: exponential backoff + jitter, retryable status/exception classification."""

from __future__ import annotations

import asyncio
import logging
import random

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from app.crawler.core.exceptions import (
    BlockedError,
    CrawlerError,
    RateLimitedError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})


@dataclass(slots=True)
class RetryPolicy:
    """Exponential backoff with full jitter.

    `on_block` / `on_rate_limit` are hooks so the caller can rotate proxy or
    apply a host cooldown between attempts without coupling retry to those
    subsystems.
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True
    retry_on: frozenset[int] = field(default=RETRYABLE_STATUS)

    def is_retryable_status(self, status: int) -> bool:
        return status in self.retry_on

    def is_retryable(self, exc: BaseException) -> bool:
        if isinstance(exc, CrawlerError):
            return exc.retryable
        return isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError))

    def delay_for(self, attempt: int, *, retry_after: float | None = None) -> float:
        """`attempt` is 1-based (delay applied *after* attempt N failed)."""
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        raw = min(self.base_delay * (self.multiplier ** (attempt - 1)), self.max_delay)
        return random.uniform(0.0, raw) if self.jitter else raw

    async def run(
        self,
        fn: Callable[[int], Awaitable[T]],
        *,
        label: str = "request",
        on_block: Callable[[BlockedError, int], Awaitable[None]] | None = None,
        on_rate_limit: Callable[[RateLimitedError, int], Awaitable[None]] | None = None,
    ) -> T:
        """Call `fn(attempt)` until it succeeds or attempts are exhausted."""
        last: BaseException | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                return await fn(attempt)
            except BaseException as exc:
                last = exc
                if not self.is_retryable(exc) or attempt == self.max_attempts:
                    raise

                retry_after: float | None = None
                if isinstance(exc, RateLimitedError):
                    retry_after = exc.retry_after
                    if on_rate_limit is not None:
                        await on_rate_limit(exc, attempt)
                elif isinstance(exc, BlockedError) and on_block is not None:
                    await on_block(exc, attempt)

                delay = self.delay_for(attempt, retry_after=retry_after)
                logger.warning(
                    "%s failed (attempt %d/%d): %s -- retrying in %.2fs",
                    label,
                    attempt,
                    self.max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        assert last is not None
        raise last
