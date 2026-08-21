"""Human-ish page interactions reused by every marketplace: lazy-load scrolling,
infinite-scroll pagination, safe waits, cookie banners."""

from __future__ import annotations

import asyncio
import logging
import random

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def human_pause(lo: float = 0.4, hi: float = 1.6) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def wait_for_any(page: Page, selectors: list[str], *, timeout: float = 15_000) -> str | None:
    """Return the first selector that appears, or None on timeout.

    Marketplaces A/B-test their DOM constantly, so callers pass several
    candidate selectors instead of one brittle string.
    """
    tasks = {
        asyncio.create_task(page.wait_for_selector(sel, timeout=timeout, state="attached")): sel for sel in selectors
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            if task.exception() is None:
                return tasks[task]
        return None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


async def scroll_page(
    page: Page,
    *,
    steps: int = 6,
    delay: tuple[float, float] = (0.3, 0.9),
    step_ratio: float = 0.85,
) -> None:
    """Scroll down in viewport-sized increments to trigger lazy-loaded content."""
    for _ in range(steps):
        await page.mouse.wheel(0, int(await page.evaluate("window.innerHeight") * step_ratio))
        await asyncio.sleep(random.uniform(*delay))


async def scroll_until_stable(
    page: Page,
    *,
    item_selector: str,
    max_items: int = 100,
    max_rounds: int = 20,
    idle_rounds: int = 3,
    delay: tuple[float, float] = (0.6, 1.4),
) -> int:
    """Infinite-scroll until `max_items` are present or the count stops growing.

    Returns the final item count. Used by TikTok Shop search (cursor-based feed)
    and Amazon's lazy grids.
    """
    previous = -1
    stale = 0

    for _ in range(max_rounds):
        count = await page.locator(item_selector).count()
        if count >= max_items:
            return count
        stale = stale + 1 if count == previous else 0
        if stale >= idle_rounds:
            return count
        previous = count

        await page.mouse.wheel(0, await page.evaluate("document.body.scrollHeight"))
        await asyncio.sleep(random.uniform(*delay))

    return await page.locator(item_selector).count()


async def click_if_present(page: Page, selector: str, *, timeout: float = 2_000) -> bool:
    """Best-effort click (cookie banners, "continue shopping", region modals)."""
    try:
        locator = page.locator(selector).first
        await locator.wait_for(state="visible", timeout=timeout)
        await locator.click()
        await human_pause(0.2, 0.6)
    except Exception:
        return False
    else:
        return True


async def dismiss_overlays(page: Page, selectors: list[str]) -> None:
    for selector in selectors:
        await click_if_present(page, selector)


async def safe_text(page: Page, selector: str, *, timeout: float = 3_000) -> str | None:
    try:
        return (await page.locator(selector).first.inner_text(timeout=timeout)).strip()
    except Exception:
        return None
