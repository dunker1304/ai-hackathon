"""Network interception: sniff internal JSON/XHR payloads while a page loads,
and block heavy resources to save proxy bandwidth."""

from __future__ import annotations

import fnmatch
import logging
import re

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page, Response, Route

    from app.crawler.core.types import JSONDict

logger = logging.getLogger(__name__)

BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
BLOCKED_URL_PATTERNS: tuple[str, ...] = (
    "*google-analytics.com*",
    "*googletagmanager.com*",
    "*doubleclick.net*",
    "*facebook.net*",
    "*hotjar.com*",
    "*sentry.io*",
    "*.mp4",
    "*.webm",
)


@dataclass(slots=True)
class CapturedResponse:
    url: str
    status: int
    body: JSONDict | list[Any]


@dataclass
class ResponseCapture:
    """Collects JSON responses whose URL matches any of `patterns` (regex).

    Both Amazon and TikTok Shop render most numbers client-side from internal
    JSON endpoints; capturing them is far more stable than parsing the DOM.

        capture = ResponseCapture([r"/api/search/", r"/product_detail"])
        await capture.attach(page)
        await page.goto(url)
        items = capture.payloads
    """

    patterns: list[str] = field(default_factory=list)
    max_items: int = 50
    responses: list[CapturedResponse] = field(default_factory=list)

    _compiled: list[re.Pattern[str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p) for p in self.patterns]

    @property
    def payloads(self) -> list[JSONDict]:
        return [r.body for r in self.responses if isinstance(r.body, dict)]

    def matches(self, url: str) -> bool:
        return not self._compiled or any(p.search(url) for p in self._compiled)

    def clear(self) -> None:
        self.responses.clear()

    async def _on_response(self, response: Response) -> None:
        if len(self.responses) >= self.max_items or not self.matches(response.url):
            return
        ctype = (response.headers or {}).get("content-type", "")
        if "json" not in ctype:
            return
        try:
            body = await response.json()
        except Exception:
            return
        self.responses.append(CapturedResponse(response.url, response.status, body))

    async def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    def detach(self, page: Page) -> None:
        try:
            page.remove_listener("response", self._on_response)
        except Exception:
            pass


async def block_resources(
    page: Page,
    *,
    resource_types: frozenset[str] = BLOCKED_RESOURCE_TYPES,
    url_patterns: tuple[str, ...] = BLOCKED_URL_PATTERNS,
) -> None:
    """Abort images/media/trackers. Camoufox's `block_images` covers images at
    the browser level; this also kills analytics beacons and video."""

    async def _route(route: Route) -> None:
        request = route.request
        if request.resource_type in resource_types:
            await route.abort()
            return
        if any(fnmatch.fnmatch(request.url, pat) for pat in url_patterns):
            await route.abort()
            return
        await route.continue_()

    await page.route("**/*", _route)
