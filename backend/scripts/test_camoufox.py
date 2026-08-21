"""Manual test harness for the Camoufox browser client.

Run from backend/:

    # smoke test the whole stack (launch -> navigate -> validate -> close)
    uv run python scripts/test_camoufox.py smoke

    # fetch any URL and inspect what the client produced
    uv run python scripts/test_camoufox.py fetch https://www.amazon.com/s?k=coffee+mug
    uv run python scripts/test_camoufox.py fetch <url> --save out.html --headful

    # capture the internal JSON APIs a page calls (the real goldmine)
    uv run python scripts/test_camoufox.py fetch <url> --capture "/api/" --capture "search"

    # check the fingerprint isn't leaking automation
    uv run python scripts/test_camoufox.py fingerprint

    # hammer N urls through the pool to verify concurrency + rotation
    uv run python scripts/test_camoufox.py bench --n 6 --pool 2

    # dump the resolved CrawlerSettings
    uv run python scripts/test_camoufox.py config

    # end-to-end Amazon flow: keyword -> product links
    uv run python scripts/test_camoufox.py amazon "coffee mug" --max-products 100
    uv run python scripts/test_camoufox.py amazon "phone case" --sort newest --save result/links.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from itertools import starmap

from app.crawler.config import CrawlerSettings, get_crawler_settings
from app.crawler.core.client.browser.client import CamoufoxClient
from app.crawler.core.client.browser.pool import BrowserPool
from app.crawler.core.client.factory import (
    build_proxy_pool,
    build_rate_limiter,
    build_retry_policy,
    build_rotator,
)
from app.crawler.core.exceptions import CrawlerError
from app.crawler.core.types import SortBy

DEFAULT_URLS = [
    "https://example.com",
    "https://httpbin.org/headers",
    "https://www.iana.org/domains/reserved",
]

# Pages that report what the browser looks like to a fingerprinter.
FINGERPRINT_PROBE = "https://abrahamjuliot.github.io/creepjs/"

log = logging.getLogger("test_camoufox")


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #


def make_settings(args: argparse.Namespace) -> CrawlerSettings:
    """Start from env-based settings, then apply CLI overrides."""
    settings = get_crawler_settings().model_copy(deep=True)
    if getattr(args, "headful", False):
        settings.headless = False
    if getattr(args, "virtual", False):
        settings.headless = "virtual"
    if getattr(args, "pool", None):
        settings.browser_pool_size = args.pool
    if getattr(args, "timeout", None):
        settings.nav_timeout_ms = args.timeout * 1000
    if getattr(args, "no_block", False):
        settings.block_images = False
        settings.block_heavy_resources = False
    if getattr(args, "proxy", None):
        settings.proxies = args.proxy
    if getattr(args, "os", None):
        settings.allowed_os = [args.os]
    if getattr(args, "rps", None):
        settings.requests_per_second = args.rps
    return settings


def make_client(settings: CrawlerSettings, *, pool_size: int | None = None) -> CamoufoxClient:
    pool = BrowserPool(
        size=pool_size or settings.browser_pool_size,
        headless=settings.headless,
        proxy_pool=build_proxy_pool(settings),
        rotator=build_rotator(settings),
        max_pages_per_slot=settings.max_pages_per_slot,
    )
    return CamoufoxClient(
        pool=pool,
        rate_limiter=build_rate_limiter(settings),
        retry=build_retry_policy(settings),
        timeout=settings.nav_timeout_ms,
        block_heavy_resources=settings.block_heavy_resources,
        owns_pool=True,
    )


def banner(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def preview(text: str, limit: int = 400) -> str:
    flat = " ".join(text.split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def save_html(path_str: str, html: str) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"\nHTML written to {path.resolve()}")


def save_json(path_str: str, payload: object) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON written to {path.resolve()}")


def report_response(res: Any, args: argparse.Namespace, elapsed: float) -> None:
    """Pretty-print everything a FetchResponse carries."""
    print(f"final url : {res.url}")
    print(f"status    : {res.status}")
    print(f"elapsed   : {elapsed:.2f}s")
    print(f"html size : {len(res.text):,} bytes")
    print(f"proxy     : {res.meta.get('proxy')}")
    print(f"captured  : {len(res.captured)} JSON payload(s)")

    if args.title:
        import re

        match = re.search(r"<title[^>]*>(.*?)</title>", res.text, re.IGNORECASE | re.DOTALL)
        print(f"title     : {match.group(1).strip() if match else '(none)'}")

    if args.selector:
        from selectolax.parser import HTMLParser

        nodes = HTMLParser(res.text).css(args.selector)
        print(f"\nselector '{args.selector}' -> {len(nodes)} node(s)")
        for node in nodes[: args.limit]:
            print(f"  - {preview(node.text(strip=True), 160)}")

    for i, payload in enumerate(res.captured[: args.limit]):
        print(f"\n--- captured[{i}] keys: {list(payload)[:12]}")
        if args.dump_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])

    if args.save:
        save_html(args.save, res.text)

    if not args.selector and not args.save:
        print(f"\npreview: {preview(res.text)}")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


async def cmd_smoke(args: argparse.Namespace) -> int:
    """Launch -> navigate -> validate -> reuse slot -> close."""
    settings = make_settings(args)
    banner("SMOKE TEST")
    print(f"headless={settings.headless}  pool=1  proxies={bool(settings.proxies)}")

    failures = 0
    started = time.monotonic()

    async with make_client(settings, pool_size=1) as client:
        for url in DEFAULT_URLS:
            t0 = time.monotonic()
            try:
                res = await client.get(url)
            except CrawlerError as exc:
                failures += 1
                print(f"  FAIL  {url}\n        {type(exc).__name__}: {exc}")
                continue
            print(
                f"  OK    {url}\n"
                f"        status={res.status} bytes={len(res.text)} "
                f"elapsed={time.monotonic() - t0:.2f}s slot={res.meta.get('slot')}"
            )

    print(f"\n{len(DEFAULT_URLS) - failures}/{len(DEFAULT_URLS)} passed in {time.monotonic() - started:.1f}s")
    print("Browser reuse works if all requests report the same slot index.")
    return 1 if failures else 0


async def cmd_fetch(args: argparse.Namespace) -> int:
    """Fetch one URL and dump everything the client produced."""
    settings = make_settings(args)
    banner(f"FETCH {args.url}")

    async with make_client(settings, pool_size=1) as client:
        on_page = None
        if args.scroll:
            from app.crawler.core.client.browser.actions import scroll_page

            async def on_page(page: Any) -> None:
                await scroll_page(page, steps=args.scroll)

        t0 = time.monotonic()
        try:
            res = await client.get(
                args.url,
                wait_for=args.wait_for,
                capture=args.capture or None,
                on_page=on_page,
                wait_until=args.wait_until,
            )
        except CrawlerError as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            return 1

        report_response(res, args, time.monotonic() - t0)

    return 0


async def cmd_fingerprint(args: argparse.Namespace) -> int:
    """Inspect the identity the browser exposes to JS. Verifies that
    webdriver is hidden and that OS/locale spoofing actually applied."""
    settings = make_settings(args)
    banner("FINGERPRINT PROBE")

    async with make_client(settings, pool_size=1) as client, client.page() as page:
        await page.goto("https://example.com", wait_until="domcontentloaded")
        info = await page.evaluate(
            """() => ({
                userAgent: navigator.userAgent,
                platform: navigator.platform,
                webdriver: navigator.webdriver,
                languages: navigator.languages,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory ?? null,
                screen: [screen.width, screen.height],
                viewport: [window.innerWidth, window.innerHeight],
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                pluginCount: navigator.plugins.length,
                playwrightGlobals: Object.getOwnPropertyNames(window).filter(
                    k => /playwright|puppeteer|__driver|cdc_|selenium/i.test(k)
                ),
            })"""
        )
        for key, value in info.items():
            print(f"  {key:20}: {value}")

        problems = []
        if info["webdriver"]:
            problems.append("navigator.webdriver is TRUE -- automation exposed")
        if info["playwrightGlobals"]:
            problems.append(f"automation globals leaked: {info['playwrightGlobals']}")
        sw, sh = info["screen"]
        vw, vh = info["viewport"]
        if vw > sw or vh > sh:
            problems.append(
                f"viewport {vw}x{vh} is larger than screen {sw}x{sh} -- impossible, "
                "widen CRAWLER_* screen constraints in FingerprintRotator"
            )

        print()
        if problems:
            for p in problems:
                print(f"  LEAK: {p}")
            return 1
        print("  No obvious automation leaks.")

        if args.creepjs:
            print(f"\nOpening {FINGERPRINT_PROBE} (takes ~30s, needs --headful to read the score)...")
            await page.goto(FINGERPRINT_PROBE, wait_until="networkidle", timeout=90_000)
            await asyncio.sleep(20)
            score = await page.evaluate(
                "() => document.querySelector('.trust-score, .fingerprint-header')?.innerText ?? 'n/a'"
            )
            print(f"  creepjs: {preview(score, 300)}")

    return 0


async def cmd_bench(args: argparse.Namespace) -> int:
    """Push N concurrent fetches through the pool: checks the semaphore, the
    rate limiter, and slot recycling."""
    settings = make_settings(args)
    banner(f"BENCH  n={args.n}  pool={settings.browser_pool_size}  rps={settings.requests_per_second}")

    urls = (args.urls or DEFAULT_URLS) * ((args.n // max(len(args.urls or DEFAULT_URLS), 1)) + 1)
    urls = urls[: args.n]

    async with make_client(settings) as client:
        t0 = time.monotonic()

        async def one(i: int, url: str) -> tuple[int, str, float, str]:
            start = time.monotonic()
            try:
                res = await client.get(url)
            except CrawlerError as exc:
                return i, url, time.monotonic() - start, f"FAIL {type(exc).__name__}"
            return i, url, time.monotonic() - start, f"ok status={res.status} slot={res.meta.get('slot')}"

        results = await asyncio.gather(*starmap(one, enumerate(urls)))
        total = time.monotonic() - t0

    for i, url, elapsed, status in sorted(results):
        print(f"  [{i:>2}] {elapsed:>6.2f}s  {status:<24} {url}")

    ok = sum(1 for *_, s in results if s.startswith("ok"))
    print(f"\n{ok}/{len(results)} ok in {total:.1f}s  ({len(results) / total:.2f} req/s)")
    return 0 if ok == len(results) else 1


async def cmd_amazon(args: argparse.Namespace) -> int:
    """keyword -> list of Amazon product links (the phase-1 crawl flow)."""
    from app.crawler.core.types import SortBy
    from app.crawler.marketplaces.amazon import AmazonCrawler, AmazonSearchClient, build_search_url

    settings = make_settings(args)
    banner(f"AMAZON LINKS  keyword={args.keyword!r}  max={args.max_products}")

    client = AmazonSearchClient(
        region=args.region,
        pool=BrowserPool(
            size=1,
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

    t0 = time.monotonic()
    async with client:
        crawler = AmazonCrawler(client, region=args.region)
        query = crawler.build_query(
            args.keyword,
            sort=SortBy(args.sort) if args.sort else None,
            department=args.department,
            min_price=args.min_price,
            max_price=args.max_price,
            min_rating=args.min_rating,
            prime_only=args.prime,
        )
        print(f"url: {build_search_url(query)}\n")

        try:
            result = await crawler.collect_product_links(
                query,
                max_products=args.max_products,
                max_pages=args.max_pages,
                include_sponsored=args.include_sponsored,
            )
        except CrawlerError as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            return 1

    print(f"\ncollected : {result.count} link(s)")
    print(f"pages     : {result.pages_fetched}")
    print(f"stopped   : {result.stopped_reason}")
    print(f"elapsed   : {time.monotonic() - t0:.1f}s")

    for link in result.links[: args.limit]:
        print(f"  #{link.position:>3} {link.asin}  {link.url}")
        if link.title:
            print(f"       {preview(link.title, 90)}")
    if result.count > args.limit:
        print(f"  ... {result.count - args.limit} more")

    if args.save:
        save_json(args.save, result.model_dump())

    return 0 if result.count else 1


async def cmd_config(args: argparse.Namespace) -> int:  # ruff: ignore[unused-async] - uniform command signature
    """Print the resolved settings and the fingerprints that would be used."""
    settings = make_settings(args)
    banner("RESOLVED CRAWLER SETTINGS")
    for key, value in settings.model_dump().items():
        print(f"  {key:24}: {value}")

    banner("SAMPLE FINGERPRINTS (3 launches)")
    rotator = build_rotator(settings)
    for i in range(3):
        fp = rotator.next()
        print(f"  [{i}] os={fp.os:<8} locale={fp.locale:<6} screen={fp.screen}")
        print(f"      camoufox kwargs: {sorted(fp.to_camoufox_kwargs())}")

    pool = build_proxy_pool(settings)
    banner(f"PROXY POOL ({len(pool.proxies)} entries, strategy={pool.strategy})")
    for p in pool.proxies:
        print(f"  {p.server}  user={p.username or '-'}  health={p.health:.2f}")
    if not pool.proxies:
        print("  (none -- set CRAWLER_PROXIES to enable rotation)")
    return 0


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_camoufox",
        description="Manual test harness for the Camoufox crawler client.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--headful", action="store_true", help="show the browser window")
    common.add_argument("--virtual", action="store_true", help="headful inside Xvfb (linux servers)")
    common.add_argument("--pool", type=int, help="browser pool size")
    common.add_argument("--timeout", type=float, help="navigation timeout in seconds")
    common.add_argument("--proxy", help="proxy string(s), comma separated")
    common.add_argument("--os", choices=["windows", "macos", "linux"], help="force spoofed OS")
    common.add_argument("--rps", type=float, help="requests per second")
    common.add_argument("--no-block", action="store_true", help="do not block images/trackers")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("smoke", parents=[common], help="end-to-end sanity check").set_defaults(fn=cmd_smoke)

    fetch = sub.add_parser("fetch", parents=[common], help="fetch a single URL")
    fetch.add_argument("url")
    fetch.add_argument("--selector", help="CSS selector to extract and print")
    fetch.add_argument("--wait-for", help="CSS selector to wait for before snapshotting")
    fetch.add_argument(
        "--wait-until",
        default="domcontentloaded",
        choices=["commit", "domcontentloaded", "load", "networkidle"],
    )
    fetch.add_argument("--capture", action="append", help="regex of XHR URLs whose JSON to capture (repeatable)")
    fetch.add_argument("--scroll", type=int, metavar="STEPS", help="scroll N steps to trigger lazy loading")
    fetch.add_argument("--save", metavar="PATH", help="write the rendered HTML to a file")
    fetch.add_argument("--title", action="store_true", help="print the <title>")
    fetch.add_argument("--dump-json", action="store_true", help="print captured JSON bodies")
    fetch.add_argument("--limit", type=int, default=10, help="max nodes/payloads to print")
    fetch.set_defaults(fn=cmd_fetch)

    fp = sub.add_parser("fingerprint", parents=[common], help="inspect the spoofed identity")
    fp.add_argument("--creepjs", action="store_true", help="also run the creepjs trust score (slow)")
    fp.set_defaults(fn=cmd_fingerprint)

    bench = sub.add_parser("bench", parents=[common], help="concurrency / rotation test")
    bench.add_argument("--n", type=int, default=6, help="number of requests")
    bench.add_argument("--urls", nargs="*", help="URLs to cycle through")
    bench.set_defaults(fn=cmd_bench)

    amazon = sub.add_parser("amazon", parents=[common], help="keyword -> Amazon product links")
    amazon.add_argument("keyword")
    amazon.add_argument("--max-products", type=int, default=500)
    amazon.add_argument("--max-pages", type=int, default=7, help="Amazon caps organic search around 7 pages")
    amazon.add_argument("--region", default="us", choices=["us", "uk", "de", "ca", "au"])
    amazon.add_argument("--sort", choices=[s.value for s in SortBy])
    amazon.add_argument("--department", help="e.g. fashion, kitchen")
    amazon.add_argument("--min-price", type=float)
    amazon.add_argument("--max-price", type=float)
    amazon.add_argument("--min-rating", type=int, choices=[4])
    amazon.add_argument("--prime", action="store_true")
    amazon.add_argument("--include-sponsored", action="store_true", help="ads are excluded by default")
    amazon.add_argument("--save", metavar="PATH", help="write the links as JSON")
    amazon.add_argument("--limit", type=int, default=20, help="how many links to print")
    amazon.set_defaults(fn=cmd_amazon)

    sub.add_parser("config", parents=[common], help="print resolved settings").set_defaults(fn=cmd_config)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("app.crawler").setLevel(logging.DEBUG if args.verbose else logging.INFO)

    try:
        return asyncio.run(args.fn(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
