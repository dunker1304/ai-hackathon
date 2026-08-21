"""Build fully-wired clients from CrawlerSettings, so marketplace code never
has to assemble pool + limiter + retry + proxies by hand."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.crawler.config import CrawlerSettings, get_crawler_settings
from app.crawler.core.client.browser.fingerprint import FingerprintRotator
from app.crawler.core.client.browser.pool import BrowserPool
from app.crawler.core.client.proxy import ProxyPool
from app.crawler.core.client.retry import RetryPolicy
from app.crawler.core.rate_limiter import RateLimiter

if TYPE_CHECKING:  # pragma: no cover - avoids importing playwright at module load
    from app.crawler.core.client.browser.client import CamoufoxClient


def build_proxy_pool(settings: CrawlerSettings | None = None) -> ProxyPool:
    s = settings or get_crawler_settings()
    return ProxyPool.from_lines(
        s.proxies,
        strategy=s.proxy_strategy,
        ban_seconds=s.proxy_ban_seconds,
    )


def build_rotator(settings: CrawlerSettings | None = None) -> FingerprintRotator:
    s = settings or get_crawler_settings()
    return FingerprintRotator(
        allowed_os=list(s.allowed_os),
        locales=list(s.locales),
        humanize=s.humanize,
        block_images=s.block_images,
        geoip=s.geoip,
    )


def build_rate_limiter(settings: CrawlerSettings | None = None) -> RateLimiter:
    s = settings or get_crawler_settings()
    return RateLimiter(
        rate=s.requests_per_second,
        burst=s.burst,
        max_concurrency=s.max_concurrency,
        per_host_concurrency=s.per_host_concurrency,
        jitter=(s.jitter_min, s.jitter_max),
    )


def build_retry_policy(settings: CrawlerSettings | None = None) -> RetryPolicy:
    s = settings or get_crawler_settings()
    return RetryPolicy(
        max_attempts=s.max_attempts,
        base_delay=s.retry_base_delay,
        max_delay=s.retry_max_delay,
    )


def build_browser_pool(settings: CrawlerSettings | None = None) -> BrowserPool:
    s = settings or get_crawler_settings()
    return BrowserPool(
        size=s.browser_pool_size,
        headless=s.headless,
        proxy_pool=build_proxy_pool(s),
        rotator=build_rotator(s),
        max_pages_per_slot=s.max_pages_per_slot,
    )


def build_camoufox_client(
    settings: CrawlerSettings | None = None,
    *,
    pool: BrowserPool | None = None,
    client_cls: type[CamoufoxClient] | None = None,
) -> CamoufoxClient:
    """Instantiate `CamoufoxClient` (or a marketplace subclass) from settings.

    Pass a shared `pool` when several marketplace clients should share browsers.
    """
    from app.crawler.core.client.browser.client import CamoufoxClient

    s = settings or get_crawler_settings()
    cls = client_cls or CamoufoxClient
    return cls(
        pool=pool or build_browser_pool(s),
        rate_limiter=build_rate_limiter(s),
        retry=build_retry_policy(s),
        timeout=s.nav_timeout_ms,
        block_heavy_resources=s.block_heavy_resources,
        owns_pool=pool is None,
    )
