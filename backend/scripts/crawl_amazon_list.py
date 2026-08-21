"""Phase 1 manual test: keyword -> Amazon product links -> result/list_data.json.

Real crawl, no mocks. Run from backend/:

    uv run python scripts/crawl_amazon_list.py "coffee mug"
    uv run python scripts/crawl_amazon_list.py "phone case" --max-products 200 --sort newest
    uv run python scripts/crawl_amazon_list.py "mug" "tumbler" "water bottle"   # several keywords

Output shape (result/list_data.json):

    {
      "crawled_at": "...", "region": "us",
      "keywords": [
        {"keyword": "coffee mug", "count": 120, "pages_fetched": 3,
         "stopped_reason": "max_products",
         "links": [{"asin": "...", "url": "...", "title": "...", "position": 1}, ...]}
      ],
      "total_links": 120
    }

The JSON is the input of `crawl_amazon_e2e.py --from-file`, so the two phases
can be run and debugged separately.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from _crawl_common import (
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
from app.crawler.core.types import SortBy
from app.crawler.marketplaces.amazon import AmazonCrawler

DEFAULT_OUTPUT = "result/list_data.json"


async def run(args: argparse.Namespace) -> int:
    settings = make_settings(
        headful=args.headful,
        pool_size=args.pool,
        proxy=args.proxy,
        rps=args.rps,
    )
    location = resolve_cli_location(args)
    client = make_amazon_client(
        settings,
        region=args.region,
        location=location,
        strict_location=not args.allow_missing_location,
    )

    banner(f"AMAZON LINK CRAWL  {len(args.keywords)} keyword(s)  max={args.max_products} each")
    where = f"{location.zip_code} ({location.label})" if location else "IP default (prices may be absent)"
    print(f"region   : {args.region}")
    print(f"location : {where}")
    print(f"headless={settings.headless}  rps={settings.requests_per_second}")

    started = time.monotonic()
    payload: dict[str, object] = {
        "crawled_at": now_iso(),
        "region": args.region,
        "location": location.zip_code if location else None,
        "max_products": args.max_products,
        "keywords": [],
    }
    total_links = 0
    failures = 0

    async with client:
        crawler = AmazonCrawler(client, region=args.region)

        for keyword in args.keywords:
            query = crawler.build_query(
                keyword,
                sort=SortBy(args.sort) if args.sort else None,
                department=args.department,
                min_price=args.min_price,
                max_price=args.max_price,
                min_rating=args.min_rating,
                prime_only=args.prime,
            )
            print(f"\n--- {keyword!r}")

            try:
                collection = await crawler.collect_product_links(
                    query,
                    max_products=args.max_products,
                    max_pages=args.max_pages,
                    include_sponsored=args.include_sponsored,
                )
            except CrawlerError as exc:
                failures += 1
                print(f"    FAILED: {type(exc).__name__}: {exc}")
                payload["keywords"].append({"keyword": keyword, "error": str(exc), "links": []})  # type: ignore[attr-defined]
                continue

            total_links += collection.count
            print(
                f"    {collection.count} links | {collection.pages_fetched} page(s) "
                f"| stopped: {collection.stopped_reason}"
            )
            for link in collection.links[: args.preview]:
                print(f"      #{link.position:>3} {link.asin}  {truncate(link.title, 58)}")
            if collection.count > args.preview:
                print(f"      ... {collection.count - args.preview} more")

            payload["keywords"].append(collection.model_dump())  # type: ignore[attr-defined]

    payload["total_links"] = total_links
    payload["elapsed_seconds"] = round(time.monotonic() - started, 1)

    path = save_json(args.output, payload)
    banner("DONE")
    print(f"keywords : {len(args.keywords)} ({failures} failed)")
    print(f"links    : {total_links}")
    print(f"elapsed  : {payload['elapsed_seconds']}s")
    print(f"saved    : {path}")
    print(f"\nnext: uv run python scripts/crawl_amazon_e2e.py --from-file {args.output}")

    return 1 if failures == len(args.keywords) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl Amazon search results into a JSON list of product links.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("keywords", nargs="*", help="one or more search keywords")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-products", type=int, default=500, help="per keyword")
    parser.add_argument("--max-pages", type=int, default=7, help="Amazon caps organic search near 7 pages")
    add_location_args(parser)
    parser.add_argument("--sort", choices=[s.value for s in SortBy])
    parser.add_argument("--department", help="e.g. fashion, kitchen")
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--min-rating", type=int, choices=[4])
    parser.add_argument("--prime", action="store_true")
    parser.add_argument("--include-sponsored", action="store_true", help="ads are excluded by default")
    parser.add_argument("--preview", type=int, default=5, help="links to print per keyword")
    parser.add_argument("--pool", type=int, help="browser pool size")
    parser.add_argument("--rps", type=float, help="requests per second")
    parser.add_argument("--proxy", help="proxy string(s), comma separated")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_locations:
        print_locations(args.region)
        return 0
    if not args.keywords:
        parser.error("at least one keyword is required")

    setup_logging(verbose=args.verbose)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
