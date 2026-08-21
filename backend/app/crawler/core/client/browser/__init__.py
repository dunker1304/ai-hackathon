"""Camoufox (anti-detect Firefox) browser transport.

from app.crawler.core.client.browser import BrowserPool, CamoufoxClient

pool = BrowserPool(size=2, headless=True)
async with CamoufoxClient(pool=pool) as client:
    res = await client.get("https://www.amazon.com/s", params={"k": "mug"})
"""

from app.crawler.core.client.browser.actions import (
    click_if_present,
    dismiss_overlays,
    human_pause,
    safe_text,
    scroll_page,
    scroll_until_stable,
    wait_for_any,
)
from app.crawler.core.client.browser.capture import ResponseCapture, block_resources
from app.crawler.core.client.browser.client import CamoufoxClient
from app.crawler.core.client.browser.detect import check_response
from app.crawler.core.client.browser.fingerprint import FingerprintRotator, FingerprintSpec
from app.crawler.core.client.browser.launcher import CamoufoxLauncher, LaunchOptions
from app.crawler.core.client.browser.pool import BrowserPool, BrowserSlot

__all__ = [
    "BrowserPool",
    "BrowserSlot",
    "CamoufoxClient",
    "CamoufoxLauncher",
    "FingerprintRotator",
    "FingerprintSpec",
    "LaunchOptions",
    "ResponseCapture",
    "block_resources",
    "check_response",
    "click_if_present",
    "dismiss_overlays",
    "human_pause",
    "safe_text",
    "scroll_page",
    "scroll_until_stable",
    "wait_for_any",
]
