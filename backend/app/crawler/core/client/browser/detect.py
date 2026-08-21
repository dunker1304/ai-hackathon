"""Anti-bot wall detection: map a loaded page / response into BlockedError,
RateLimitedError or NotFoundError before the parser ever sees it."""

from __future__ import annotations

import re

from app.crawler.core.exceptions import BlockedError, NotFoundError, RateLimitedError

# Generic interstitials (Cloudflare, Akamai, PerimeterX, DataDome).
GENERIC_BLOCK_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"just a moment\.\.\.",
        r"checking your browser before accessing",
        r"cf-browser-verification",
        r"attention required!\s*\|\s*cloudflare",
        r"access denied",
        r"unusual traffic from your (computer|network)",
        r"px-captcha",
        r"captcha-delivery\.com",
        r"verify you are (a )?human",
        # Akamai Bot Manager interstitial: returns HTTP 200 with a meta-refresh
        # challenge page, so status alone never reveals it.
        r"bm-verify",
        r"triggerInterstitialChallenge",
        r"/_sec/verify\?provider=",
        # Amazon "Robot Check"
        r"api-services-support@amazon\.com",
        r"/errors/validateCaptcha",
    )
)

RATE_LIMIT_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"too many requests",
        r"request rate exceeded",
        r"slow down",
    )
)

NOT_FOUND_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"page not found",
        r"we couldn't find that page",
        r"this item is no longer available",
    )
)


def find_marker(html: str, markers: tuple[re.Pattern[str], ...], *, window: int = 60_000) -> str | None:
    """Search only the head of the document; walls short-circuit early."""
    head = html[:window]
    for pattern in markers:
        if pattern.search(head):
            return pattern.pattern
    return None


def check_response(
    *,
    url: str,
    status: int,
    html: str,
    extra_block_markers: tuple[re.Pattern[str], ...] = (),
    min_length: int = 500,
) -> None:
    """Raise the appropriate CrawlerError, or return None when the page looks
    usable. Marketplace clients pass their own `extra_block_markers`
    (e.g. Amazon "Robot Check", TikTok `status_code: 10000`)."""
    if status == 404:
        raise NotFoundError("Page not found", url=url, status=status)
    if status == 429:
        raise RateLimitedError("HTTP 429", url=url, status=status)
    if status in (403, 503):
        raise BlockedError(f"HTTP {status} interstitial", url=url, status=status)

    marker = find_marker(html, RATE_LIMIT_MARKERS)
    if marker:
        raise RateLimitedError(f"Throttle page detected ({marker})", url=url, status=status)

    marker = find_marker(html, GENERIC_BLOCK_MARKERS + extra_block_markers)
    if marker:
        raise BlockedError(f"Anti-bot wall detected ({marker})", url=url, status=status)

    marker = find_marker(html, NOT_FOUND_MARKERS)
    if marker:
        raise NotFoundError(f"Not found page ({marker})", url=url, status=status)

    if len(html) < min_length:
        raise BlockedError(f"Suspiciously small document ({len(html)} bytes)", url=url, status=status)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
