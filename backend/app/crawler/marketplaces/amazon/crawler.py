"""AmazonCrawler: composes Amazon clients + parsers into search / bestseller / product / keyword crawl flows."""

from __future__ import annotations

import asyncio
import logging
import time

from itertools import starmap
from typing import TYPE_CHECKING

from app.crawler.core.exceptions import BlockedError, CrawlerError, ParseError
from app.crawler.marketplaces.amazon import constants as const
from app.crawler.marketplaces.amazon.parsers.product import parse_product_page
from app.crawler.marketplaces.amazon.parsers.search import parse_search_page
from app.crawler.marketplaces.amazon.schemas import (
    AmazonProduct,
    LinkCollection,
    ProductBatch,
    ProductLink,
)
from app.crawler.marketplaces.amazon.url import SearchQuery

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.crawler.core.types import SortBy, TimeWindow
    from app.crawler.marketplaces.amazon.client import AmazonClient
    from app.crawler.marketplaces.amazon.client.search import AmazonSearchClient

logger = logging.getLogger(__name__)

DEFAULT_MAX_PRODUCTS = 500
DEFAULT_DETAIL_CONCURRENCY = 2


class AmazonCrawler:
    """Keyword -> product links -> product details.

        async with AmazonClient(pool=pool) as client:
            crawler = AmazonCrawler(client)
            links = await crawler.collect_product_links("coffee mug", max_products=500)
            batch = await crawler.fetch_product_details(links.links)

    Or in one call:

            batch = await crawler.crawl_keyword("coffee mug", max_products=100)

    The crawler owns pagination, dedupe and concurrency; the client owns
    transport; the parser owns extraction. Each is testable on its own.
    """

    def __init__(self, client: AmazonSearchClient | AmazonClient, *, region: str = const.DEFAULT_REGION) -> None:
        self.client = client
        self.region = region

    def build_query(
        self,
        keyword: str,
        *,
        sort: SortBy | None = None,
        department: str | None = None,
        category_node: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        min_rating: int | None = None,
        prime_only: bool = False,
        time_window: TimeWindow | None = None,
    ) -> SearchQuery:
        return SearchQuery(
            keyword=keyword,
            region=self.region,
            sort=sort,
            department=department,
            category_node=category_node,
            min_price=min_price,
            max_price=max_price,
            min_rating=min_rating,
            prime_only=prime_only,
            time_window=time_window,
        )

    async def collect_product_links(
        self,
        keyword: str | SearchQuery,
        *,
        max_products: int = DEFAULT_MAX_PRODUCTS,
        max_pages: int = const.MAX_SEARCH_PAGES,
        include_sponsored: bool = False,
        stop_on_error: bool = False,
    ) -> LinkCollection:
        """Walk the SERP pages, aggregating unique product links.

        Stops on the first of: `max_products` reached, no next page, `max_pages`
        reached, or a page that yields no new ASINs (Amazon repeats the last
        page instead of 404-ing past the end).

        Sponsored placements are excluded by default -- they are paid
        positioning and would distort the demand metrics downstream.
        """
        query = keyword if isinstance(keyword, SearchQuery) else self.build_query(keyword)

        links: list[ProductLink] = []
        seen: set[str] = set()
        pages_fetched = 0
        reason = "max_pages"

        for page in range(1, max_pages + 1):
            try:
                response = await self.client.fetch_search_page(query, page=page)
                parsed = parse_search_page(
                    response.text,
                    url=response.url,
                    page=page,
                    region=self.region,
                    keyword=query.keyword,
                    include_sponsored=include_sponsored,
                    start_position=len(links) + 1,
                )
            except (BlockedError, ParseError) as exc:
                logger.warning("Page %d failed for %r: %s", page, query.keyword, exc)
                if stop_on_error or not links:
                    raise
                reason = f"error_on_page_{page}: {type(exc).__name__}"
                break
            except CrawlerError as exc:
                logger.warning("Page %d failed for %r: %s", page, query.keyword, exc)
                if stop_on_error:
                    raise
                reason = f"error_on_page_{page}: {type(exc).__name__}"
                break

            pages_fetched += 1
            fresh = [link for link in parsed.links if link.asin not in seen]
            seen.update(link.asin for link in fresh)
            links.extend(fresh)

            logger.info(
                "page=%d parsed=%d new=%d total=%d/%d",
                page,
                len(parsed.links),
                len(fresh),
                len(links),
                max_products,
            )

            if len(links) >= max_products:
                links = links[:max_products]
                reason = "max_products"
                break
            if not fresh:
                reason = "no_new_results"
                break
            if not parsed.has_next:
                reason = "no_next_page"
                break
        else:
            reason = "max_pages"

        # Positions were assigned per page; renumber after truncation.
        for index, link in enumerate(links, start=1):
            link.position = index

        logger.info(
            "Collected %d links for %r across %d page(s) (%s)",
            len(links),
            query.keyword,
            pages_fetched,
            reason,
        )
        return LinkCollection(
            keyword=query.keyword,
            links=links,
            pages_fetched=pages_fetched,
            stopped_reason=reason,
        )

    # -- phase 2: detail pages ---------------------------------------------

    async def fetch_product_detail(self, link: ProductLink | str) -> AmazonProduct:
        """Fetch and parse a single /dp page.

        Accepts either a `ProductLink` (so keyword/position survive into the
        record) or a bare ASIN.
        """
        if isinstance(link, str):
            asin, keyword, position = link, None, None
        else:
            asin, keyword, position = link.asin, link.keyword, link.position

        response = await self.client.fetch_product_page(asin)  # type: ignore[union-attr]
        return parse_product_page(
            response.text,
            url=response.url,
            keyword=keyword,
            position=position,
            region=self.region,
            expected_currency=const.REGION_CURRENCIES.get(self.region),
        )

    async def fetch_product_details(
        self,
        links: Sequence[ProductLink | str],
        *,
        concurrency: int = DEFAULT_DETAIL_CONCURRENCY,
        stop_on_error: bool = False,
        on_product: Callable[[AmazonProduct], None] | None = None,
    ) -> ProductBatch:
        """Crawl many detail pages, keeping partial results.

        Concurrency is deliberately low and bounded by a semaphore: the browser
        pool and the rate limiter already throttle, but firing 500 coroutines at
        once would queue them all inside the limiter and make failures arrive in
        one useless burst.

        Failures are collected per ASIN rather than aborting the batch -- with
        hundreds of pages some blocks are inevitable, and 480 good records beat
        an exception.
        """
        started = time.monotonic()
        semaphore = asyncio.Semaphore(max(1, concurrency))
        products: list[AmazonProduct] = []
        failed: dict[str, str] = {}
        total = len(links)

        async def worker(index: int, link: ProductLink | str) -> None:
            asin = link if isinstance(link, str) else link.asin
            async with semaphore:
                try:
                    product = await self.fetch_product_detail(link)
                except CrawlerError as exc:
                    logger.warning("[%d/%d] %s failed: %s", index, total, asin, exc)
                    failed[asin] = f"{type(exc).__name__}: {exc}"
                    if stop_on_error:
                        raise
                    return

            products.append(product)
            logger.info(
                "[%d/%d] %s price=%s %s rating=%s reviews=%s bought=%s conf=%.2f",
                index,
                total,
                product.asin,
                product.price,
                product.currency or "",
                product.rating,
                product.review_count,
                product.bought_past_month,
                product.parse_confidence,
            )
            if on_product is not None:
                on_product(product)

        await asyncio.gather(*starmap(worker, enumerate(links, start=1)))

        # gather() completion order is nondeterministic; restore SERP ranking.
        products.sort(key=lambda p: (p.position is None, p.position or 0))

        batch = ProductBatch(
            products=products,
            failed=failed,
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
        logger.info(
            "Fetched %d/%d detail pages in %.1fs (%.0f%% success)",
            batch.count,
            total,
            batch.elapsed_seconds,
            batch.success_rate * 100,
        )
        return batch

    # -- phase 1 + 2 --------------------------------------------------------

    async def crawl_keyword(
        self,
        keyword: str | SearchQuery,
        *,
        max_products: int = DEFAULT_MAX_PRODUCTS,
        max_pages: int = const.MAX_SEARCH_PAGES,
        include_sponsored: bool = False,
        concurrency: int = DEFAULT_DETAIL_CONCURRENCY,
        on_product: Callable[[AmazonProduct], None] | None = None,
    ) -> tuple[LinkCollection, ProductBatch]:
        """End-to-end: keyword -> links -> full product records."""
        collection = await self.collect_product_links(
            keyword,
            max_products=max_products,
            max_pages=max_pages,
            include_sponsored=include_sponsored,
        )
        batch = await self.fetch_product_details(
            collection.links,
            concurrency=concurrency,
            on_product=on_product,
        )
        return collection, batch
