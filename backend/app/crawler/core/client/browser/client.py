"""CamoufoxClient: BaseClient implementation backed by a Camoufox browser pool.

Wires together pool + rate limiter + retry + proxy rotation + block detection,
and returns the same `FetchResponse` as the plain HTTP client so parsers do not
care how the bytes were obtained.
"""

from __future__ import annotations

import logging
import time

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from app.crawler.core.client.base import BaseClient, FetchResponse
from app.crawler.core.client.browser.capture import ResponseCapture, block_resources
from app.crawler.core.client.browser.detect import check_response
from app.crawler.core.client.browser.pool import BrowserPool, BrowserSlot
from app.crawler.core.client.retry import RetryPolicy
from app.crawler.core.exceptions import (
    BlockedError,
    BrowserError,
    NavigationTimeoutError,
    RateLimitedError,
)
from app.crawler.core.rate_limiter import RateLimiter

if TYPE_CHECKING:  # pragma: no cover - typing only
    import re

    from collections.abc import AsyncIterator, Awaitable, Callable

    from playwright.async_api import Page

    from app.crawler.core.types import Headers, QueryParams, WaitUntil

logger = logging.getLogger(__name__)


class CamoufoxClient(BaseClient):
    """Headless anti-detect fetcher.

        async with CamoufoxClient(pool=BrowserPool(size=2)) as client:
            res = await client.get("https://www.amazon.com/s", params={"k": "mug"})
            html = res.text

    Marketplace clients subclass this to add their own block markers, overlay
    selectors and JSON capture patterns (see `block_markers`,
    `overlay_selectors`, `capture_patterns`).
    """

    #: extra regexes that mean "we got walled" for this marketplace
    block_markers: tuple[re.Pattern[str], ...] = ()
    #: cookie / region / login modals to dismiss after navigation
    overlay_selectors: tuple[str, ...] = ()
    #: URL regexes whose JSON responses should be captured during navigation
    capture_patterns: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        pool: BrowserPool | None = None,
        rate_limiter: RateLimiter | None = None,
        retry: RetryPolicy | None = None,
        timeout: float = 45_000.0,
        wait_until: WaitUntil = "domcontentloaded",
        block_heavy_resources: bool = True,
        default_headers: Headers | None = None,
        owns_pool: bool | None = None,
    ) -> None:
        self.pool = pool or BrowserPool()
        self.rate_limiter = rate_limiter or RateLimiter(rate=0.5, burst=2, max_concurrency=2)
        self.retry = retry or RetryPolicy(max_attempts=3, base_delay=2.0)
        self.timeout = timeout
        self.wait_until: WaitUntil = wait_until
        self.block_heavy_resources = block_heavy_resources
        self.default_headers = default_headers or {}
        self._owns_pool = owns_pool if owns_pool is not None else pool is None
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        await self.pool.start()
        self._started = True

    async def close(self) -> None:
        if not self._started:
            return
        if self._owns_pool:
            await self.pool.close()
        self._started = False

    # -- hooks for marketplace subclasses ----------------------------------

    async def prepare_page(self, page: Page, slot: BrowserSlot) -> None:
        """Called once per page before navigation (headers, cookies, init scripts)."""
        if self.default_headers:
            await page.set_extra_http_headers(self.default_headers)
        if self.block_heavy_resources:
            await block_resources(page)

    async def after_navigate(self, page: Page, slot: BrowserSlot) -> None:
        """Called after `goto` succeeds: dismiss modals, wait for hydration."""
        if self.overlay_selectors:
            from app.crawler.core.client.browser.actions import dismiss_overlays

            await dismiss_overlays(page, list(self.overlay_selectors))

    def validate(self, *, url: str, status: int, html: str) -> None:
        """Raise Blocked/RateLimited/NotFound before parsing. Override to add
        marketplace-specific sentinels."""
        check_response(url=url, status=status, html=html, extra_block_markers=self.block_markers)

    # -- core fetch --------------------------------------------------------

    @staticmethod
    def _build_url(url: str, params: QueryParams | None) -> str:
        if not params:
            return url
        clean = {k: v for k, v in params.items() if v is not None}
        if not clean:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{urlencode(clean)}"

    async def get(
        self,
        url: str,
        *,
        params: QueryParams | None = None,
        headers: Headers | None = None,
        wait_until: WaitUntil | None = None,
        wait_for: str | list[str] | None = None,
        capture: list[str] | None = None,
        on_page: Callable[[Page], Awaitable[None]] | None = None,
        timeout: float | None = None,
        **_: Any,
    ) -> FetchResponse:
        """Navigate to `url` and return the rendered HTML plus captured JSON.

        `on_page` runs after navigation & validation — use it for scrolling,
        pagination clicks or extra waits before the HTML snapshot is taken.
        """
        target = self._build_url(url, params)

        async def attempt(_: int) -> FetchResponse:
            async with self.rate_limiter.slot(target):
                return await self._fetch_once(
                    target,
                    headers=headers,
                    wait_until=wait_until or self.wait_until,
                    wait_for=wait_for,
                    capture=capture,
                    on_page=on_page,
                    timeout=timeout or self.timeout,
                )

        # async by contract: RetryPolicy awaits these hooks.
        async def on_block(exc: BlockedError, attempt_no: int) -> None:  # ruff: ignore[unused-async]
            logger.warning("Blocked on %s (attempt %d): %s", target, attempt_no, exc)
            self.rate_limiter.penalize(target, 30.0)

        async def on_rate_limit(exc: RateLimitedError, attempt_no: int) -> None:  # ruff: ignore[unused-async]
            self.rate_limiter.penalize(target, exc.retry_after or 60.0)

        return await self.retry.run(
            attempt,
            label=f"GET {target}",
            on_block=on_block,
            on_rate_limit=on_rate_limit,
        )

    async def _fetch_once(
        self,
        url: str,
        *,
        headers: Headers | None,
        wait_until: WaitUntil,
        wait_for: str | list[str] | None,
        capture: list[str] | None,
        on_page: Callable[[Page], Awaitable[None]] | None,
        timeout: float,
    ) -> FetchResponse:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        started = time.monotonic()
        patterns = capture if capture is not None else list(self.capture_patterns)
        sniffer = ResponseCapture(patterns) if patterns else None

        async with self.pool.page() as (page, slot):
            try:
                await self.prepare_page(page, slot)
                if headers:
                    await page.set_extra_http_headers(headers)
                if sniffer is not None:
                    await sniffer.attach(page)

                response = await page.goto(url, wait_until=wait_until, timeout=timeout)
                status = response.status if response is not None else 0
                resp_headers = dict(response.headers) if response is not None else {}

                await self.after_navigate(page, slot)
                await self._wait_for(page, wait_for, timeout=timeout)

                html = await page.content()
                self.validate(url=page.url, status=status, html=html)

                if on_page is not None:
                    await on_page(page)
                    html = await page.content()

            except PlaywrightTimeout as exc:
                slot.pages_served += 1
                raise NavigationTimeoutError(f"Navigation timed out: {exc}", url=url) from exc
            except (BlockedError, RateLimitedError):
                await self.pool.recycle(slot, ban_proxy=True)
                raise
            except PlaywrightError as exc:
                await self.pool.recycle(slot)
                raise BrowserError(f"Playwright error: {exc}", url=url) from exc
            else:
                self.pool.proxy_pool.report_success(slot.proxy)

            return FetchResponse(
                url=page.url,
                status=status,
                text=html,
                headers=resp_headers,
                elapsed=time.monotonic() - started,
                from_browser=True,
                captured=sniffer.payloads if sniffer else [],
                meta={"slot": slot.index, "proxy": slot.proxy.server if slot.proxy else None},
            )

    @staticmethod
    async def _wait_for(page: Page, wait_for: str | list[str] | None, *, timeout: float) -> None:
        if wait_for is None:
            return
        if isinstance(wait_for, str):
            await page.wait_for_selector(wait_for, timeout=timeout, state="attached")
            return
        from app.crawler.core.client.browser.actions import wait_for_any

        await wait_for_any(page, wait_for, timeout=timeout)

    # -- escape hatch ------------------------------------------------------

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        """Raw page access for multi-step flows (login, filter clicks, pagination)
        that don't fit the single-`get` model."""
        if not self._started:
            await self.start()
        async with self.pool.page() as (page, slot):
            await self.prepare_page(page, slot)
            yield page
