"""End-to-end manual test: keyword -> links -> full product details.

Real crawl against Amazon, no mocks. Run from backend/:

    # full flow from a keyword
    uv run python scripts/crawl_amazon_e2e.py "coffee mug" --max-products 20

    # reuse the links crawled earlier (skips phase 1, much faster to iterate)
    uv run python scripts/crawl_amazon_e2e.py --from-file result/list_data.json --limit 20

    # a specific product
    uv run python scripts/crawl_amazon_e2e.py --asin B0721C21RJ

Writes result/detail_data.json and prints a summary table plus a data-quality
report, which is the point of the exercise: a crawl that "succeeds" while
returning empty prices is worse than one that fails loudly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from pathlib import Path
from typing import TYPE_CHECKING

from _crawl_common import (
    BACKEND_DIR,
    add_location_args,
    banner,
    make_amazon_client,
    make_settings,
    now_iso,
    print_locations,
    resolve_cli_location,
    save_json,
    setup_logging,
    truncate,
)

from app.crawler.core.exceptions import CrawlerError
from app.crawler.marketplaces.amazon import AmazonCrawler, ProductLink
from app.crawler.marketplaces.amazon.constants import REGION_CURRENCIES

if TYPE_CHECKING:
    from app.crawler.marketplaces.amazon.schemas import AmazonProduct, LinkCollection, ProductBatch

DEFAULT_OUTPUT = "result/detail_data.json"


def load_links(path: str, *, limit: int | None, keyword: str | None) -> list[ProductLink]:
    """Read the output of crawl_amazon_list.py."""
    target = Path(path)
    if not target.is_absolute():
        target = BACKEND_DIR / target
    if not target.exists():
        raise SystemExit(f"{target} not found -- run scripts/crawl_amazon_list.py first")

    payload = json.loads(target.read_text(encoding="utf-8"))
    links: list[ProductLink] = []
    for entry in payload.get("keywords", []):
        if keyword and entry.get("keyword") != keyword:
            continue
        links.extend(ProductLink(**raw) for raw in entry.get("links", []))
    return links[:limit] if limit else links


def quality_report(batch: ProductBatch) -> dict[str, object]:
    """Field-level coverage. A high success rate with empty fields means the
    selectors drifted, which the success rate alone would hide."""
    products = batch.products
    if not products:
        return {}

    total = len(products)

    def coverage(field: str) -> float:
        return round(sum(getattr(p, field) is not None for p in products) / total, 3)

    currencies: dict[str, int] = {}
    for product in products:
        if product.currency:
            currencies[product.currency] = currencies.get(product.currency, 0) + 1

    return {
        "products": total,
        "failed": len(batch.failed),
        "success_rate": round(batch.success_rate, 3),
        "coverage": {
            field: coverage(field)
            for field in ("title", "brand", "price", "rating", "review_count", "bought_past_month", "image_url")
        },
        "with_bsr": round(sum(bool(p.best_seller_ranks) for p in products) / total, 3),
        "mean_confidence": round(sum(p.parse_confidence for p in products) / total, 3),
        "currencies": currencies,
        # Priceless because Amazon refused to ship there -- a location problem,
        # not a parser problem. Separating the two makes the fix obvious.
        "unshippable": sum(p.unshippable for p in products),
    }


def print_products(products: list[AmazonProduct], limit: int) -> None:
    print(f"\n{'ASIN':<12} {'PRICE':>10} {'RATE':>5} {'REVIEWS':>9} {'BOUGHT':>8} {'BSR':>7}  TITLE")
    print("-" * 110)
    for product in products[:limit]:
        rank = product.primary_rank
        price = f"{product.currency or ''}{product.price:,.2f}" if product.price is not None else "-"
        print(
            f"{product.asin:<12} {price:>10} "
            f"{product.rating if product.rating is not None else '-':>5} "
            f"{product.review_count if product.review_count is not None else '-':>9} "
            f"{product.bought_past_month if product.bought_past_month is not None else '-':>8} "
            f"{rank.rank if rank else '-':>7}  {truncate(product.title, 44)}"
        )
    if len(products) > limit:
        print(f"... {len(products) - limit} more")


def print_quality(report: dict[str, object], *, region: str, location_label: str) -> list[str]:
    """Print the report and return the warnings worth acting on."""
    expected = REGION_CURRENCIES.get(region, "USD")

    banner("DATA QUALITY")
    print(f"products      : {report['products']}  (failed: {report['failed']})")
    print(f"success rate  : {report['success_rate']:.0%}")
    print(f"confidence    : {report['mean_confidence']:.2f}")
    print(f"with BSR      : {report['with_bsr']:.0%}")
    print(f"currencies    : {report['currencies']}  (expected {expected})")
    print(f"location      : {location_label}")
    if report.get("unshippable"):
        print(f"unshippable   : {report['unshippable']}  <-- no buybox at this delivery address")
    print("field coverage:")
    for field, value in report["coverage"].items():  # type: ignore[union-attr]
        flag = "" if value >= 0.8 else "  <-- low"
        print(f"  {field:20} {value:>6.0%}{flag}")

    warnings: list[str] = []
    currencies = report["currencies"]
    if isinstance(currencies, dict):
        if set(currencies) - {expected}:
            warnings.append(
                f"prices are not all in {expected} ({currencies}). The delivery location did not "
                f"take effect, so Amazon is quoting the currency of the exit IP; these figures "
                f"are not comparable with other marketplaces."
            )
        if len(currencies) > 1:
            warnings.append("mixed currencies inside one batch -- do not aggregate these prices.")

    unshippable = report.get("unshippable") or 0
    if isinstance(unshippable, int) and unshippable:
        warnings.append(
            f"{unshippable} product(s) have no buybox because they do not ship to "
            f"{location_label}. Their price is unknown, not zero. Try another "
            f"--location inside the same storefront."
        )

    coverage = report["coverage"]
    if isinstance(coverage, dict):
        if coverage.get("price", 1) < 0.8 and not unshippable:
            warnings.append("price coverage below 80% with no shipping blocks -- buybox selector may have drifted.")
        if coverage.get("bought_past_month", 0) < 0.3:
            warnings.append(
                "few listings expose 'bought in past month'; Amazon hides it for low-volume "
                "products, so demand must be derived from BSR + reviews instead."
            )
    return warnings


def print_failures(batch: ProductBatch) -> None:
    if not batch.failed:
        return
    banner(f"FAILURES ({len(batch.failed)})")
    for asin, error in list(batch.failed.items())[:10]:
        print(f"  {asin}: {truncate(error, 90)}")


async def resolve_links(
    crawler: AmazonCrawler, args: argparse.Namespace
) -> tuple[list[ProductLink | str], LinkCollection | None]:
    """Pick the link source: explicit ASINs, a saved file, or a live phase-1 crawl."""
    if args.asin:
        banner(f"AMAZON DETAIL  {len(args.asin)} ASIN(s)")
        return list(args.asin), None

    if args.from_file:
        links = load_links(args.from_file, limit=args.limit, keyword=args.keyword_filter)
        banner(f"AMAZON DETAIL  {len(links)} link(s) from {args.from_file}")
        return list(links), None

    banner(f"AMAZON E2E  keyword={args.keyword!r}  max={args.max_products}")
    collection = await crawler.collect_product_links(
        args.keyword,
        max_products=args.max_products,
        max_pages=args.max_pages,
    )
    print(f"phase 1: {collection.count} links in {collection.pages_fetched} page(s) ({collection.stopped_reason})")
    return list(collection.links), collection


async def run(args: argparse.Namespace) -> int:
    settings = make_settings(headful=args.headful, pool_size=args.pool, proxy=args.proxy, rps=args.rps)
    location = resolve_cli_location(args)
    client = make_amazon_client(
        settings,
        region=args.region,
        location=location,
        strict_location=not args.allow_missing_location,
    )
    print(
        f"region={args.region}  location="
        f"{f'{location.zip_code} ({location.label})' if location else 'IP default (prices may be absent)'}"
    )

    started = time.monotonic()

    async with client:
        crawler = AmazonCrawler(client, region=args.region)
        links, collection = await resolve_links(crawler, args)
        if not links:
            print("no links to crawl")
            return 1

        print(f"phase 2: fetching {len(links)} detail page(s), concurrency={args.concurrency}\n")
        try:
            batch = await crawler.fetch_product_details(links, concurrency=args.concurrency)
        except CrawlerError as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            return 1

    if not batch.products:
        print("\nno products parsed")
        for asin, error in list(batch.failed.items())[:5]:
            print(f"  {asin}: {error}")
        return 1

    print_products(batch.products, args.preview)

    report = quality_report(batch)
    location_label = f"{location.zip_code} ({location.label})" if location else "IP default"
    warnings = print_quality(report, region=args.region, location_label=location_label)
    print_failures(batch)

    payload = {
        "crawled_at": now_iso(),
        "region": args.region,
        "location": location.zip_code if location else None,
        "keyword": args.keyword if not args.from_file and not args.asin else None,
        "quality": report,
        "warnings": warnings,
        "products": [p.model_dump() for p in batch.products],
        "failed": batch.failed,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    if collection is not None:
        payload["links"] = collection.model_dump()

    path = save_json(args.output, payload)

    banner("DONE")
    print(f"products : {batch.count}")
    print(f"elapsed  : {payload['elapsed_seconds']}s  ({batch.elapsed_seconds}s in phase 2)")
    print(f"saved    : {path}")

    if warnings:
        print("\nwarnings:")
        for warning in warnings:
            print(f"  - {warning}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="End-to-end Amazon crawl: keyword -> links -> product details.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("keyword", nargs="?", help="search keyword (runs both phases)")
    source.add_argument("--from-file", metavar="PATH", help="reuse links from crawl_amazon_list.py")
    source.add_argument("--asin", nargs="+", help="crawl specific ASINs only")

    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-products", type=int, default=20, help="phase-1 cap; detail pages are slow")
    parser.add_argument("--max-pages", type=int, default=7)
    parser.add_argument("--limit", type=int, help="cap links read from --from-file")
    parser.add_argument("--keyword-filter", help="with --from-file: only this keyword's links")
    parser.add_argument("--concurrency", type=int, default=2, help="parallel detail fetches")
    add_location_args(parser)
    parser.add_argument("--preview", type=int, default=20, help="rows to print")
    parser.add_argument("--pool", type=int, help="browser pool size")
    parser.add_argument("--rps", type=float)
    parser.add_argument("--proxy")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_locations:
        print_locations(args.region)
        return 0
    if not (args.keyword or args.from_file or args.asin):
        parser.error("give a keyword, --from-file or --asin")

    setup_logging(verbose=args.verbose)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
