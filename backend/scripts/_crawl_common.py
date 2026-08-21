"""Shared plumbing for the manual crawl scripts.

Keeps `crawl_amazon_list.py` and `crawl_amazon_e2e.py` down to the flow itself
instead of repeating client wiring and JSON dumping.
"""

from __future__ import annotations

import json
import logging
import sys

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.crawler.config import CrawlerSettings, get_crawler_settings  # ruff: ignore[module-import-not-at-top-of-file]
from app.crawler.core.client.browser.pool import BrowserPool  # ruff: ignore[module-import-not-at-top-of-file]
from app.crawler.core.client.factory import (  # ruff: ignore[module-import-not-at-top-of-file]
    build_proxy_pool,
    build_rate_limiter,
    build_retry_policy,
    build_rotator,
)
from app.crawler.core.exceptions import CrawlerError  # ruff: ignore[module-import-not-at-top-of-file]
from app.crawler.marketplaces.amazon import AmazonClient  # ruff: ignore[module-import-not-at-top-of-file]
from app.crawler.marketplaces.amazon.location import (  # ruff: ignore[module-import-not-at-top-of-file]
    DEFAULT_LOCATIONS,
    NAMED_LOCATIONS,
    ZIP_EXAMPLES,
    DeliveryLocation,
    available_presets,
    resolve_location,
)

if TYPE_CHECKING:
    import argparse

RESULT_DIR = BACKEND_DIR / "result"


def setup_logging(*, verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("app.crawler.core.client.browser.pool").setLevel(logging.WARNING)


def make_settings(
    *,
    headful: bool = False,
    pool_size: int | None = None,
    proxy: str | None = None,
    rps: float | None = None,
) -> CrawlerSettings:
    settings = get_crawler_settings().model_copy(deep=True)
    if headful:
        settings.headless = False
    if pool_size:
        settings.browser_pool_size = pool_size
    if proxy:
        settings.proxies = proxy
    if rps:
        settings.requests_per_second = rps
    return settings


def add_location_args(parser: argparse.ArgumentParser) -> None:
    """Delivery-location flags, shared by every Amazon script."""
    group = parser.add_argument_group("delivery location")
    group.add_argument(
        "--region",
        default="us",
        choices=sorted(DEFAULT_LOCATIONS),
        help="Amazon storefront (default: us)",
    )
    group.add_argument(
        "--location",
        metavar="ZIP|PRESET",
        help=(
            "delivery location inside that storefront: a postcode (90210, 'SW1A 1AA') "
            "or a preset. 'none' disables it. Amazon hides prices for addresses it "
            "cannot ship to, so this must match --region"
        ),
    )
    group.add_argument(
        "--allow-missing-location",
        action="store_true",
        help="continue even if the delivery location cannot be applied (prices will be incomplete)",
    )
    group.add_argument(
        "--list-locations",
        action="store_true",
        help="print the presets for --region and exit",
    )


def print_locations(region: str) -> None:
    default = DEFAULT_LOCATIONS[region]
    print(f"\nPresets for --region {region} (default: {default.zip_code} / {default.label}):\n")
    for name in available_presets(region):
        preset = NAMED_LOCATIONS[region][name]
        print(f"  {name:<14} {preset.zip_code:<10} {preset.label}")
    print(f"\nOr pass any {region.upper()} postcode, e.g. --location {ZIP_EXAMPLES[region]!r}")
    print("Use --location none to accept whatever Amazon infers from your IP.\n")


def resolve_cli_location(args: argparse.Namespace) -> DeliveryLocation | None:
    """Validate --location early so a bad postcode fails before the browser starts."""
    try:
        return resolve_location(args.region, args.location)
    except CrawlerError as exc:
        raise SystemExit(f"error: {exc}") from exc


def make_amazon_client(
    settings: CrawlerSettings,
    *,
    region: str = "us",
    location: DeliveryLocation | None = None,
    strict_location: bool = True,
) -> AmazonClient:
    """One client, one browser pool, shared by the search and detail phases.

    Opening a second pool for the detail pass would show Amazon two different
    fingerprints for what is logically one browsing session.
    """
    return AmazonClient(
        region=region,
        location=location,
        strict_location=strict_location,
        pool=BrowserPool(
            size=settings.browser_pool_size,
            headless=settings.headless,
            proxy_pool=build_proxy_pool(settings),
            rotator=build_rotator(settings),
            max_pages_per_slot=settings.max_pages_per_slot,
        ),
        rate_limiter=build_rate_limiter(settings),
        retry=build_retry_policy(settings),
        timeout=settings.nav_timeout_ms,
        block_heavy_resources=settings.block_heavy_resources,
        owns_pool=True,
    )


def save_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = BACKEND_DIR / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def banner(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def truncate(text: str | None, limit: int = 70) -> str:
    if not text:
        return ""
    flat = " ".join(text.split())
    return flat[:limit] + ("..." if len(flat) > limit else "")
