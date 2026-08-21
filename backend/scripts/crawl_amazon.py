"""Crawl Amazon search results via ScraperAPI into data/listings_amazon.csv.

Replaces the discontinued Helium10 export as the Amazon listing source. The
output keeps the exact column names the ``ADAPTERS`` seam in scripts/seed.py
already expects (Title, Price, Sales, Review Count, Seller, URL), so seeding
is unchanged: crawl first, then ``uv run python scripts/seed.py``.

"Sales" is approximated from Amazon's "N+ bought in past month" badge — the
closest public signal to Helium10's sales estimate. Listings without the badge
get 0; rows without a title or parseable price are dropped (never guessed).

Search queries default to the 42 product-type names in data/taxonomy.json.

Run from backend/ (needs SCRAPERAPI_KEY in .env):

    uv run python scripts/crawl_amazon.py                 # all taxonomy queries
    uv run python scripts/crawl_amazon.py --max-queries 5 # smoke test / save credits
    uv run python scripts/crawl_amazon.py --query "acrylic ornament" --pages 2
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_OUTPUT = DATA_DIR / "listings_amazon_test.csv"
TAXONOMY_FILE = DATA_DIR / "taxonomy.json"

SEARCH_ENDPOINT = "https://api.scraperapi.com/structured/amazon/search"
CSV_COLUMNS = ["Title", "Price", "Sales", "Review Count", "Seller", "URL"]

# ScraperAPI advises long client timeouts: a request may be retried
# server-side against several proxies before responding.
REQUEST_TIMEOUT_SECONDS = 70
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
DEFAULT_DELAY_SECONDS = 1.0
WEEKS_PER_MONTH = 4

_BOUGHT_RE = re.compile(
    r"([\d.,]+)\s*([KkMm]?)\+?\s*bought in past\s+(month|week)"
)
_PRICE_RE = re.compile(r"[\d.,]+")


def parse_bought_count(message: str | None) -> int:
    """Turn Amazon's "2K+ bought in past month" badge into a monthly count."""
    if not message:
        return 0
    match = _BOUGHT_RE.search(message)
    if not match:
        return 0
    number = float(match.group(1).replace(",", ""))
    multiplier = {"k": 1_000, "m": 1_000_000}.get(match.group(2).lower(), 1)
    count = number * multiplier
    if match.group(3) == "week":
        count *= WEEKS_PER_MONTH
    return int(count)


def _parse_price(result: dict) -> float | None:
    price = result.get("price")
    if isinstance(price, (int, float)) and price > 0:
        return float(price)
    price_string = result.get("price_string")
    if not isinstance(price_string, str):
        return None
    match = _PRICE_RE.search(price_string)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return 0


def row_from_result(result: dict) -> dict | None:
    """Map one ScraperAPI search result onto a listings_amazon.csv row.

    Returns None (row dropped) when the title or price is missing — a
    listing without either is useless to scoring and must not be guessed.
    """
    title = result.get("name") or result.get("title")
    if not title:
        return None
    price = _parse_price(result)
    if price is None:
        return None
    return {
        "Title": str(title).strip(),
        "Price": round(price, 2),
        "Sales": parse_bought_count(result.get("purchase_history_message")),
        "Review Count": _parse_int(result.get("total_reviews")),
        # Search results carry no seller; seed.py tolerates the empty string.
        "Seller": str(result.get("seller") or "").strip(),
        "URL": str(result.get("url") or "").strip(),
    }


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Drop rows whose URL was already seen (same product ranking for
    multiple queries). Rows without a URL are all kept."""
    seen: set[str] = set()
    unique: list[dict] = []
    for row in rows:
        url = row.get("URL") or ""
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        unique.append(row)
    return unique


def build_queries(taxonomy: list[dict]) -> list[str]:
    return [entry["name"] for entry in taxonomy if entry.get("name")]


def fetch_search_page(
    client: httpx.Client,
    api_key: str,
    query: str,
    page: int,
    country: str,
) -> list[dict]:
    """One ScraperAPI structured Amazon search call, with retries."""
    params = {
        "api_key": api_key,
        "query": query,
        "page": page,
        "country": country,
        "tld": "com",
    }
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.get(SEARCH_ENDPOINT, params=params)
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results")
            if not isinstance(results, list):
                raise ValueError(
                    f"unexpected payload shape, top-level keys: {sorted(payload)[:6]}"
                )
            return results
        except Exception as exc:  # noqa: BLE001 - retried, then surfaced to caller
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(f"    attempt {attempt} failed ({exc}); retrying in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"ScraperAPI request failed after {MAX_RETRIES} attempts") from last_error


def write_csv(rows: list[dict], output: Path) -> None:
    """Write atomically: the old export is only replaced once the new file
    is fully on disk, so a crash mid-crawl never truncates seed input."""
    fd, tmp_name = tempfile.mkstemp(dir=output.parent, suffix=".csv.tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        tmp_path.replace(output)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _load_api_key() -> str:
    key = os.environ.get("SCRAPERAPI_KEY")
    if not key:
        try:
            from app.config import settings

            key = settings.scraperapi_key
        except Exception as exc:  # noqa: BLE001 - settings need a full .env
            print(f"  note: could not load app settings ({str(exc)[:80]})")
    if not key:
        sys.exit(
            "SCRAPERAPI_KEY is not set. Add it to backend/.env "
            "(see .env.example) or export it, then re-run."
        )
    return key


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--query",
        action="append",
        default=None,
        help="explicit search query (repeatable); default: all taxonomy names",
    )
    parser.add_argument("--pages", type=int, default=1, help="result pages per query (default 1)")
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="crawl only the first N queries (smoke tests / credit budget)",
    )
    parser.add_argument("--country", default="us", help="ScraperAPI country code (default us)")
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"seconds between requests (default {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output CSV path (default {DEFAULT_OUTPUT})",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    api_key = _load_api_key()

    if args.query:
        queries = args.query
    else:
        queries = build_queries(json.loads(TAXONOMY_FILE.read_text()))
    if args.max_queries is not None:
        queries = queries[: args.max_queries]
    if not queries:
        sys.exit("no queries to crawl")

    total_requests = len(queries) * args.pages
    print(f"crawling {len(queries)} queries x {args.pages} page(s) = {total_requests} requests")

    rows: list[dict] = []
    failed_queries: list[str] = []
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for i, query in enumerate(queries, 1):
            print(f"[{i}/{len(queries)}] {query!r}")
            for page in range(1, args.pages + 1):
                try:
                    results = fetch_search_page(client, api_key, query, page, args.country)
                except Exception as exc:  # noqa: BLE001 - skip query, keep crawling
                    print(f"  FAILED page {page}: {exc}")
                    failed_queries.append(query)
                    break
                page_rows = [r for r in map(row_from_result, results) if r is not None]
                dropped = len(results) - len(page_rows)
                print(f"  page {page}: {len(page_rows)} rows ({dropped} dropped)")
                rows.extend(page_rows)
                time.sleep(args.delay)

    rows = dedupe_rows(rows)
    if not rows:
        sys.exit("no listings crawled — existing CSV left untouched")

    write_csv(rows, args.out)
    print(f"\nwrote {len(rows)} listings -> {args.out}")
    if failed_queries:
        print(f"WARNING: {len(failed_queries)} queries failed: {failed_queries}")
    print("next: uv run python scripts/seed.py")


if __name__ == "__main__":
    main()
