"""Use-cases the API calls: validate a request, create a session, queue it."""

from __future__ import annotations

import logging
import uuid

from typing import TYPE_CHECKING, Any

from app.crawl.repository import CrawlRepository
from app.crawler.core.exceptions import CrawlerError
from app.crawler.marketplaces.amazon.location import resolve_location
from app.models import CrawlSession, CrawlStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_KEYWORDS = 10
MAX_PRODUCTS = 500


def validate_request(
    keywords: list[str],
    region: str,
    location: str | None,
    max_products: int,
) -> tuple[list[str], str | None]:
    """Reject bad input before a browser is ever launched.

    The delivery location is resolved here, not in the worker: a postcode from
    the wrong country is accepted by Amazon and then ignored, producing a crawl
    where every price is null. Failing at request time makes that a 400 instead
    of a twenty-minute mystery.
    """
    cleaned = [" ".join(k.split()) for k in keywords if k.strip()]
    if not cleaned:
        raise ValueError("At least one keyword is required")
    if len(cleaned) > MAX_KEYWORDS:
        raise ValueError(f"At most {MAX_KEYWORDS} keywords per session")
    if not 1 <= max_products <= MAX_PRODUCTS:
        raise ValueError(f"max_products must be between 1 and {MAX_PRODUCTS}")

    try:
        resolved = resolve_location(region, location)
    except CrawlerError as exc:
        raise ValueError(str(exc)) from exc

    return cleaned, resolved.zip_code if resolved else None


def create_session(
    db: Session,
    *,
    keywords: list[str],
    region: str = "us",
    location: str | None = None,
    max_products: int = 100,
    options: dict[str, Any] | None = None,
    marketplace: str = "amazon",
) -> CrawlSession:
    """Persist a queued session. Does not start it."""
    cleaned, zip_code = validate_request(keywords, region, location, max_products)

    return CrawlRepository(db, marketplace=marketplace).create(
        session_id=uuid.uuid4().hex,
        keywords=cleaned,
        region=region,
        location=zip_code,
        max_products=max_products,
        options=options or {},
    )


def start_session(db: Session, session: CrawlSession) -> str | None:
    """Queue the session on Celery and record the task id.

    Returns the task id, or None when the broker is unreachable -- the session
    is then marked FAILED rather than left PENDING forever, so the status page
    shows something actionable.
    """
    from app.crawl.tasks import crawl_amazon

    try:
        result = crawl_amazon.delay(session.id)
    except Exception as exc:
        logger.exception("Could not queue crawl %s", session.id)
        CrawlRepository(db).mark_failed(session.id, f"Could not queue the crawl: {exc}")
        return None

    session.task_id = result.id
    db.commit()
    return result.id


def cancel_session(db: Session, session: CrawlSession) -> bool:
    """Revoke a running crawl.

    `terminate=True` kills the worker process: the crawl sits inside
    `asyncio.run` driving a browser, so a cooperative signal would not be seen
    until the current page finished.
    """
    if CrawlStatus(session.status).is_terminal:
        return False

    if session.task_id:
        from app.crawl.celery_app import celery_app

        celery_app.control.revoke(session.task_id, terminate=True, signal="SIGTERM")

    CrawlRepository(db).mark_cancelled(session.id)
    return True
