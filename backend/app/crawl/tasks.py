"""Celery tasks that run a crawl end to end and stream progress into Postgres."""

from __future__ import annotations

import asyncio
import logging

from typing import TYPE_CHECKING, Any

from celery.exceptions import SoftTimeLimitExceeded

from app.crawl.celery_app import celery_app
from app.crawl.quality import quality_report
from app.crawl.repository import CrawlRepository
from app.crawler.config import get_crawler_settings
from app.crawler.core.client.browser.pool import BrowserPool
from app.crawler.core.client.factory import (
    build_proxy_pool,
    build_rate_limiter,
    build_retry_policy,
    build_rotator,
)
from app.crawler.core.exceptions import CrawlerError
from app.crawler.core.types import SortBy
from app.crawler.marketplaces.amazon import AmazonClient, AmazonCrawler
from app.crawler.marketplaces.amazon.location import resolve_location
from app.db import SessionLocal
from app.models import CrawlStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.crawler.marketplaces.amazon.schemas import AmazonProduct, ProductLink

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="crawl.amazon")
def crawl_amazon(self, session_id: str) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Run the crawl described by `session_id`.

    The session row is the single source of truth for both the request and the
    progress, so a browser refresh or a worker restart never loses the state.
    """
    logger.info("Starting crawl session %s", session_id)
    try:
        return asyncio.run(_run(session_id, task_id=self.request.id))
    except SoftTimeLimitExceeded:
        _fail(session_id, "Crawl exceeded its time limit")
        raise
    except Exception as exc:
        logger.exception("Crawl session %s failed", session_id)
        _fail(session_id, f"{type(exc).__name__}: {exc}")
        raise


def _fail(session_id: str, message: str) -> None:
    with SessionLocal() as db:
        try:
            CrawlRepository(db).mark_failed(session_id, message)
        except LookupError:
            logger.warning("Session %s vanished before its failure could be recorded", session_id)


async def _run(session_id: str, *, task_id: str | None) -> dict[str, Any]:
    with SessionLocal() as db:
        repo = CrawlRepository(db)
        session = repo.require(session_id)
        request = {
            "keywords": list(session.keywords),
            "region": session.region,
            "location": session.location,
            "max_products": session.max_products,
            "options": dict(session.options),
        }
        repo.mark_started(session_id, task_id=task_id)

    client = _build_client(request["region"], request["location"])

    async with client:
        crawler = AmazonCrawler(client, region=request["region"])
        links = await _discover(session_id, crawler, request)

        if not links:
            with SessionLocal() as db:
                CrawlRepository(db).mark_completed(
                    session_id,
                    warnings=["No products found for these keywords"],
                )
            return {"session_id": session_id, "products": 0}

        products = await _fetch_details(session_id, crawler, links)

    report, warnings = quality_report(products, region=request["region"], location=request["location"])
    with SessionLocal() as db:
        CrawlRepository(db).mark_completed(session_id, quality=report, warnings=warnings)

    logger.info("Crawl session %s finished: %d products", session_id, len(products))
    return {"session_id": session_id, "products": len(products), "quality": report}


def _build_client(region: str, location: str | None) -> AmazonClient:
    settings = get_crawler_settings()
    return AmazonClient(
        region=region,
        location=resolve_location(region, location),
        pool=BrowserPool(
            size=1,  # one browser per task; scale with worker processes
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


async def _discover(
    session_id: str,
    crawler: AmazonCrawler,
    request: dict[str, Any],
) -> list[ProductLink]:
    """Phase 1. A keyword that fails is recorded and skipped: with several
    keywords, one block should not discard the rest."""
    options = request["options"]
    all_links: list[ProductLink] = []
    seen: set[str] = set()

    for keyword in request["keywords"]:
        with SessionLocal() as db:
            CrawlRepository(db).mark_phase(
                session_id,
                CrawlStatus.DISCOVERING,
                f"searching {keyword!r}",
            )

        query = crawler.build_query(
            keyword,
            sort=SortBy(options["sort"]) if options.get("sort") else None,
            department=options.get("department"),
            min_price=options.get("min_price"),
            max_price=options.get("max_price"),
            min_rating=options.get("min_rating"),
            prime_only=bool(options.get("prime_only")),
        )

        try:
            collection = await crawler.collect_product_links(
                query,
                max_products=request["max_products"],
                include_sponsored=bool(options.get("include_sponsored")),
            )
        except CrawlerError as exc:
            logger.warning("Keyword %r failed: %s", keyword, exc)
            with SessionLocal() as db:
                from app.crawler.marketplaces.amazon.schemas import LinkCollection

                CrawlRepository(db).save_links(
                    session_id,
                    LinkCollection(keyword=keyword, stopped_reason="error"),
                    error=f"{type(exc).__name__}: {exc}",
                )
            continue

        with SessionLocal() as db:
            CrawlRepository(db).save_links(session_id, collection)

        for link in collection.links:
            if link.asin not in seen:
                seen.add(link.asin)
                all_links.append(link)

    with SessionLocal() as db:
        CrawlRepository(db).set_links_total(
            session_id,
            links_found=len(all_links),
            products_total=len(all_links),
        )
    return all_links


async def _fetch_details(
    session_id: str,
    crawler: AmazonCrawler,
    links: list[ProductLink],
) -> list[AmazonProduct]:
    """Phase 2. Each product is committed as it arrives so the progress page
    can show partial results while the crawl is still running."""

    def on_product(product: AmazonProduct) -> None:
        with SessionLocal() as db:
            repo = CrawlRepository(db)
            repo.save_product(session_id, product)
            repo.bump_progress(session_id, done=1)

    batch = await crawler.fetch_product_details(links, on_product=on_product)

    if batch.failed:
        with SessionLocal() as db:
            repo = CrawlRepository(db)
            for asin, error in batch.failed.items():
                repo.save_detail_error(session_id, asin, error)
            repo.bump_progress(session_id, failed=len(batch.failed))

    return batch.products
