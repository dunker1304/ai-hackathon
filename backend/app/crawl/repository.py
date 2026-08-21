"""All database writes for a crawl session, in one place.

The worker calls this from inside an event loop, so every method is short and
commits immediately: holding a transaction open across a 12-second page fetch
would pin a connection for the whole crawl and make the progress page read
stale rows.
"""

from __future__ import annotations

import logging

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import CrawlKeyword, CrawlProduct, CrawlSession, CrawlStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from app.crawler.marketplaces.amazon.schemas import AmazonProduct, LinkCollection, ProductLink

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class CrawlRepository:
    """Persistence for one crawl run."""

    def __init__(self, db: Session, *, marketplace: str = "amazon") -> None:
        self.db = db
        self.marketplace = marketplace

    # -- session lifecycle -------------------------------------------------

    def get(self, session_id: str) -> CrawlSession | None:
        return self.db.get(CrawlSession, session_id)

    def require(self, session_id: str) -> CrawlSession:
        session = self.get(session_id)
        if session is None:
            raise LookupError(f"Crawl session {session_id!r} does not exist")
        return session

    def create(
        self,
        *,
        session_id: str,
        keywords: list[str],
        region: str,
        location: str | None,
        max_products: int,
        options: dict[str, Any] | None = None,
    ) -> CrawlSession:
        session = CrawlSession(
            id=session_id,
            marketplace=self.marketplace,
            keywords=keywords,
            region=region,
            location=location,
            max_products=max_products,
            options=options or {},
            status=CrawlStatus.PENDING,
        )
        self.db.add(session)
        self.db.commit()
        return session

    def mark_started(self, session_id: str, *, task_id: str | None = None) -> None:
        self._update(
            session_id,
            status=CrawlStatus.DISCOVERING,
            task_id=task_id,
            started_at=_now(),
            phase_detail="starting browser",
        )

    def mark_phase(self, session_id: str, status: CrawlStatus, detail: str | None = None) -> None:
        self._update(session_id, status=status, phase_detail=detail)

    def mark_failed(self, session_id: str, error: str) -> None:
        self._update(
            session_id,
            status=CrawlStatus.FAILED,
            error=error[:2000],
            finished_at=_now(),
            phase_detail=None,
        )

    def mark_cancelled(self, session_id: str) -> None:
        self._update(session_id, status=CrawlStatus.CANCELLED, finished_at=_now(), phase_detail=None)

    def mark_completed(
        self,
        session_id: str,
        *,
        quality: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self._update(
            session_id,
            status=CrawlStatus.COMPLETED,
            quality=quality or {},
            warnings=warnings or [],
            finished_at=_now(),
            phase_detail=None,
        )

    def _update(self, session_id: str, **values: Any) -> None:
        session = self.require(session_id)
        for key, value in values.items():
            setattr(session, key, value)
        self.db.commit()

    # -- phase 1 -----------------------------------------------------------

    def save_links(self, session_id: str, collection: LinkCollection, *, error: str | None = None) -> int:
        """Store one keyword's discovery result plus its link rows.

        Links land in `crawl_products` immediately so a crawl that dies during
        phase 2 still leaves usable rows behind.
        """
        self.db.add(
            CrawlKeyword(
                session_id=session_id,
                keyword=collection.keyword,
                links_found=collection.count,
                pages_fetched=collection.pages_fetched,
                stopped_reason=collection.stopped_reason,
                error=error,
            )
        )
        written = self._upsert_links(session_id, collection.links)
        self.db.commit()
        return written

    def _upsert_links(self, session_id: str, links: Sequence[ProductLink]) -> int:
        if not links:
            return 0

        rows = [
            {
                "session_id": session_id,
                "marketplace": self.marketplace,
                "external_id": link.asin,
                "url": link.url,
                "keyword": link.keyword,
                "position": link.position,
                "page": link.page,
                "sponsored": link.sponsored,
                "title": link.title,
            }
            for link in links
        ]

        # Several keywords can surface the same ASIN; the first hit keeps its
        # position, which is the better ranking signal.
        statement = (
            insert(CrawlProduct).values(rows).on_conflict_do_nothing(constraint="uq_crawl_products_session_external")
        )
        result = self.db.execute(statement)
        return result.rowcount or 0

    def set_links_total(self, session_id: str, *, links_found: int, products_total: int) -> None:
        self._update(
            session_id,
            links_found=links_found,
            products_total=products_total,
            status=CrawlStatus.FETCHING,
            phase_detail=f"fetching {products_total} detail pages",
        )

    # -- phase 2 -----------------------------------------------------------

    def save_product(self, session_id: str, product: AmazonProduct) -> None:
        """Merge detail fields onto the link row created in phase 1.

        An upsert rather than an update: `--asin` runs skip discovery entirely,
        so the row may not exist yet.
        """
        values = {
            "session_id": session_id,
            "marketplace": self.marketplace,
            "external_id": product.asin,
            "url": product.url,
            "keyword": product.keyword,
            "position": product.position,
            "title": product.title,
            "brand": product.brand,
            "price": product.price,
            "list_price": product.list_price,
            "currency": product.currency,
            "rating": product.rating,
            "review_count": product.review_count,
            "bought_past_month": product.bought_past_month,
            "availability": product.availability,
            "unshippable": product.unshippable,
            "best_seller_ranks": [r.model_dump() for r in product.best_seller_ranks],
            "categories": product.categories,
            "bullets": product.bullets,
            "attributes": product.attributes,
            "image_url": product.image_url,
            "parent_asin": product.parent_asin,
            "variation_count": product.variation_count,
            "parse_confidence": product.parse_confidence,
            "fetched_at": product.fetched_at,
        }
        # Discovery-only columns (page, sponsored) are absent here and must not
        # be overwritten with NULL.
        updatable = {k: v for k, v in values.items() if k not in {"session_id", "external_id", "marketplace"}}

        statement = (
            insert(CrawlProduct)
            .values(values)
            .on_conflict_do_update(
                constraint="uq_crawl_products_session_external",
                set_=updatable,
            )
        )
        self.db.execute(statement)
        self.db.commit()

    def save_detail_error(self, session_id: str, external_id: str, error: str) -> None:
        statement = (
            insert(CrawlProduct)
            .values(
                session_id=session_id,
                marketplace=self.marketplace,
                external_id=external_id,
                url=f"https://www.amazon.com/dp/{external_id}",
                detail_error=error[:1000],
            )
            .on_conflict_do_update(
                constraint="uq_crawl_products_session_external",
                set_={"detail_error": error[:1000]},
            )
        )
        self.db.execute(statement)
        self.db.commit()

    def bump_progress(self, session_id: str, *, done: int = 0, failed: int = 0) -> None:
        """Increment counters in SQL so concurrent detail workers cannot clobber
        each other with a read-modify-write."""
        session = self.require(session_id)
        session.products_done += done
        session.products_failed += failed
        self.db.commit()

    # -- reads -------------------------------------------------------------

    def products(self, session_id: str, *, limit: int | None = None) -> list[CrawlProduct]:
        statement = (
            select(CrawlProduct)
            .where(CrawlProduct.session_id == session_id)
            .order_by(CrawlProduct.position.nulls_last(), CrawlProduct.id)
        )
        if limit:
            statement = statement.limit(limit)
        return list(self.db.scalars(statement))

    def keywords(self, session_id: str) -> list[CrawlKeyword]:
        return list(
            self.db.scalars(
                select(CrawlKeyword).where(CrawlKeyword.session_id == session_id).order_by(CrawlKeyword.id)
            )
        )

    def recent_sessions(self, *, limit: int = 20) -> list[CrawlSession]:
        return list(self.db.scalars(select(CrawlSession).order_by(CrawlSession.created_at.desc()).limit(limit)))
