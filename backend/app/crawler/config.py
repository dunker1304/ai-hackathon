"""CrawlerSettings (pydantic-settings): timeouts, concurrency, rate limits, proxy pool, browser/fingerprint options."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.crawler.core.types import BrowserOS


class CrawlerSettings(BaseSettings):
    """Env-driven crawler config. All keys are prefixed with `CRAWLER_`."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="crawler_", extra="ignore")

    # --- browser ---
    browser_pool_size: int = 2
    headless: bool | Literal["virtual"] = True
    max_pages_per_slot: int = 40
    nav_timeout_ms: float = 45_000.0
    block_images: bool = False  # browser-level blocking is a WAF signal; see fingerprint.py
    block_heavy_resources: bool = True  # route-level abort of images/media/trackers
    humanize: bool = True

    # --- fingerprint ---
    allowed_os: list[BrowserOS] = ["windows", "macos"]
    locales: list[str] = ["en-US"]
    geoip: bool = False

    # --- throughput ---
    requests_per_second: float = 0.5
    burst: int = 2
    max_concurrency: int = 2
    per_host_concurrency: int = 2
    jitter_min: float = 0.5
    jitter_max: float = 2.0

    # --- retry ---
    max_attempts: int = 3
    retry_base_delay: float = 2.0
    retry_max_delay: float = 30.0

    # --- proxy ---
    proxies: str | None = None  # newline/comma separated
    proxy_strategy: Literal["round_robin", "random", "healthiest"] = "round_robin"
    proxy_ban_seconds: float = 300.0


@lru_cache
def get_crawler_settings() -> CrawlerSettings:
    return CrawlerSettings()
