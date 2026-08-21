"""Pool of Camoufox browser slots. Each slot owns its own process, fingerprint
and proxy, and is recycled after N pages or on a block."""

from __future__ import annotations

import asyncio
import itertools
import logging

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.crawler.core.client.browser.fingerprint import FingerprintRotator
from app.crawler.core.client.browser.launcher import CamoufoxLauncher, LaunchOptions
from app.crawler.core.client.proxy import Proxy, ProxyPool
from app.crawler.core.exceptions import BrowserPoolExhaustedError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import AsyncIterator

    from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)


@dataclass
class BrowserSlot:
    """One browser process + its live context."""

    index: int
    launcher: CamoufoxLauncher
    proxy: Proxy | None = None
    context: BrowserContext | None = None
    pages_served: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def ready(self) -> bool:
        return self.context is not None and self.launcher.running


class BrowserPool:
    """Bounded pool of Camoufox instances.

        pool = BrowserPool(size=2, proxy_pool=proxies)
        await pool.start()
        async with pool.page() as page:
            await page.goto(url)
        await pool.close()

    Slots start lazily so an idle crawler costs nothing. `max_pages_per_slot`
    forces periodic identity rotation, which matters more than raw speed on
    Amazon and TikTok Shop.
    """

    def __init__(
        self,
        *,
        size: int = 2,
        headless: bool | str = True,
        proxy_pool: ProxyPool | None = None,
        rotator: FingerprintRotator | None = None,
        base_options: LaunchOptions | None = None,
        max_pages_per_slot: int = 40,
        acquire_timeout: float = 120.0,
    ) -> None:
        if size < 1:
            raise ValueError("Pool size must be >= 1")
        self.size = size
        self.headless = headless
        self.proxy_pool = proxy_pool or ProxyPool()
        self.rotator = rotator or FingerprintRotator()
        self.base_options = base_options
        self.max_pages_per_slot = max_pages_per_slot
        self.acquire_timeout = acquire_timeout

        self._slots: list[BrowserSlot] = []
        self._free: asyncio.Queue[BrowserSlot] = asyncio.Queue()
        self._started = False
        self._closing = False
        self._seq = itertools.count()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._slots = [BrowserSlot(index=i, launcher=self._new_launcher()) for i in range(self.size)]
        for slot in self._slots:
            self._free.put_nowait(slot)
        self._started = True
        logger.info("BrowserPool ready (size=%d, headless=%s)", self.size, self.headless)

    async def close(self) -> None:
        if not self._started:
            return
        self._closing = True
        await asyncio.gather(*(self._teardown(slot) for slot in self._slots), return_exceptions=True)
        self._slots.clear()
        while not self._free.empty():
            self._free.get_nowait()
        self._started = False
        self._closing = False

    def _new_launcher(self) -> CamoufoxLauncher:
        return CamoufoxLauncher(
            headless=self.headless,
            rotator=self.rotator,
            base_options=self.base_options,
        )

    # -- slot management ---------------------------------------------------

    async def _ensure_ready(self, slot: BrowserSlot) -> None:
        if slot.ready:
            return
        slot.proxy = await self.proxy_pool.acquire()
        browser = await slot.launcher.start(proxy=slot.proxy, seed=f"slot-{slot.index}-{next(self._seq)}")
        slot.context = await browser.new_context()
        slot.pages_served = 0

    async def _teardown(self, slot: BrowserSlot) -> None:
        if slot.context is not None:
            try:
                await slot.context.close()
            except Exception:
                logger.debug("Context close failed for slot %d", slot.index, exc_info=True)
            slot.context = None
        await slot.launcher.stop()

    async def recycle(self, slot: BrowserSlot, *, ban_proxy: bool = False) -> None:
        """Kill the process and force a fresh fingerprint + proxy next time."""
        logger.info("Recycling slot %d (ban_proxy=%s)", slot.index, ban_proxy)
        if ban_proxy:
            self.proxy_pool.report_failure(slot.proxy, ban=True)
        await self._teardown(slot)
        slot.launcher = self._new_launcher()
        slot.proxy = None
        slot.pages_served = 0

    # -- acquisition -------------------------------------------------------

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[BrowserSlot]:
        if not self._started:
            await self.start()
        try:
            slot = await asyncio.wait_for(self._free.get(), timeout=self.acquire_timeout)
        except TimeoutError as exc:
            raise BrowserPoolExhaustedError(f"No browser slot available after {self.acquire_timeout}s") from exc

        try:
            async with slot.lock:
                await self._ensure_ready(slot)
                yield slot
        finally:
            if not self._closing:
                if slot.pages_served >= self.max_pages_per_slot:
                    await self.recycle(slot)
                self._free.put_nowait(slot)

    @asynccontextmanager
    async def page(self) -> AsyncIterator[tuple[Page, BrowserSlot]]:
        """Yield a fresh page bound to a pooled context."""
        async with self.slot() as slot:
            assert slot.context is not None
            page = await slot.context.new_page()
            slot.pages_served += 1
            try:
                yield page, slot
            finally:
                try:
                    await page.close()
                except Exception:
                    logger.debug("Page close failed", exc_info=True)
