"""Crawl API: start a session, poll its progress, read its results."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.crawl.repository import CrawlRepository
from app.crawl.service import MAX_KEYWORDS, MAX_PRODUCTS, cancel_session, create_session, start_session
from app.crawler.marketplaces.amazon.location import DEFAULT_LOCATIONS, NAMED_LOCATIONS, available_presets
from app.db import get_db
from app.models import CrawlProduct, CrawlSession, CrawlStatus

router = APIRouter(prefix="/crawl", tags=["crawl"])

DbSession = Annotated[Session, Depends(get_db)]


# --- schemas ---------------------------------------------------------------


class CrawlOptions(BaseModel):
    sort: Literal["relevance", "best_seller", "newest", "price_asc", "price_desc", "rating"] | None = None
    department: str | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    min_rating: Literal[4] | None = None
    prime_only: bool = False
    include_sponsored: bool = False


class StartCrawlRequest(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS)
    region: Literal["us", "uk", "de", "ca", "au"] = "us"
    #: Postcode or preset ("los-angeles"). Must belong to `region`: Amazon hides
    #: prices for addresses it cannot ship to. Omit for the storefront default.
    location: str | None = None
    max_products: int = Field(default=100, ge=1, le=MAX_PRODUCTS)
    options: CrawlOptions = Field(default_factory=CrawlOptions)


class KeywordProgress(BaseModel):
    keyword: str
    links_found: int
    pages_fetched: int
    stopped_reason: str | None
    error: str | None


class CrawlSessionResponse(BaseModel):
    id: str
    marketplace: str
    keywords: list[str]
    region: str
    location: str | None
    max_products: int
    status: CrawlStatus
    phase_detail: str | None
    progress: float = Field(description="0.0-1.0 for a progress bar")
    links_found: int
    products_done: int
    products_failed: int
    products_total: int | None
    error: str | None
    quality: dict[str, Any]
    warnings: list[str]
    created_at: Any
    started_at: Any
    finished_at: Any
    keyword_progress: list[KeywordProgress] = Field(default_factory=list)

    @classmethod
    def build(cls, session: CrawlSession, keywords: list[Any] | None = None) -> CrawlSessionResponse:
        return cls(
            id=session.id,
            marketplace=session.marketplace,
            keywords=list(session.keywords),
            region=session.region,
            location=session.location,
            max_products=session.max_products,
            status=CrawlStatus(session.status),
            phase_detail=session.phase_detail,
            progress=session.progress,
            links_found=session.links_found,
            products_done=session.products_done,
            products_failed=session.products_failed,
            products_total=session.products_total,
            error=session.error,
            quality=session.quality or {},
            warnings=list(session.warnings or []),
            created_at=session.created_at,
            started_at=session.started_at,
            finished_at=session.finished_at,
            keyword_progress=[
                KeywordProgress(
                    keyword=k.keyword,
                    links_found=k.links_found,
                    pages_fetched=k.pages_fetched,
                    stopped_reason=k.stopped_reason,
                    error=k.error,
                )
                for k in (keywords or [])
            ],
        )


class ProductResponse(BaseModel):
    external_id: str
    url: str
    title: str | None
    brand: str | None
    price: float | None
    #: Always read together with `price`: Amazon quotes the currency of the
    #: exit IP, so a bare number is ambiguous.
    currency: str | None
    rating: float | None
    review_count: int | None
    #: A floor ("500+" -> 500). `null` means Amazon did not show it, which is
    #: not the same as zero.
    bought_past_month: int | None
    #: When true, `price is null` means "not shown at this delivery address".
    unshippable: bool
    best_seller_ranks: list[dict]
    image_url: str | None
    keyword: str | None
    position: int | None
    sponsored: bool
    parse_confidence: float
    estimated_monthly_revenue: float | None
    detail_error: str | None

    @classmethod
    def build(cls, product: CrawlProduct) -> ProductResponse:
        return cls(
            external_id=product.external_id,
            url=product.url,
            title=product.title,
            brand=product.brand,
            price=product.price,
            currency=product.currency,
            rating=product.rating,
            review_count=product.review_count,
            bought_past_month=product.bought_past_month,
            unshippable=product.unshippable,
            best_seller_ranks=list(product.best_seller_ranks or []),
            image_url=product.image_url,
            keyword=product.keyword,
            position=product.position,
            sponsored=product.sponsored,
            parse_confidence=product.parse_confidence,
            estimated_monthly_revenue=product.estimated_monthly_revenue,
            detail_error=product.detail_error,
        )


# --- endpoints -------------------------------------------------------------


@router.get("/locations")
def list_locations() -> dict[str, Any]:
    """Valid delivery locations per storefront.

    The UI should populate its location picker from this: a postcode from the
    wrong country is silently ignored by Amazon and every price comes back null.
    """
    return {
        region: {
            "default": {"zip": location.zip_code, "label": location.label},
            "presets": [
                {
                    "name": name,
                    "zip": NAMED_LOCATIONS[region][name].zip_code,
                    "label": NAMED_LOCATIONS[region][name].label,
                }
                for name in available_presets(region)
            ],
        }
        for region, location in DEFAULT_LOCATIONS.items()
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def start_crawl(payload: StartCrawlRequest, db: DbSession) -> CrawlSessionResponse:
    """Queue a crawl and return immediately with its session id.

    The client then polls `GET /crawl/{id}` to render the progress page.
    """
    try:
        session = create_session(
            db,
            keywords=payload.keywords,
            region=payload.region,
            location=payload.location,
            max_products=payload.max_products,
            options=payload.options.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    start_session(db, session)
    db.refresh(session)
    return CrawlSessionResponse.build(session)


@router.get("")
def list_crawls(db: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[CrawlSessionResponse]:
    return [CrawlSessionResponse.build(s) for s in CrawlRepository(db).recent_sessions(limit=limit)]


@router.get("/{session_id}")
def get_crawl(session_id: str, db: DbSession) -> CrawlSessionResponse:
    """Poll this for the progress page. Every counter comes from Postgres, so
    it survives a worker restart and matches the rows already written."""
    repo = CrawlRepository(db)
    session = repo.get(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Crawl session {session_id} not found")
    return CrawlSessionResponse.build(session, repo.keywords(session_id))


@router.get("/{session_id}/products")
def get_crawl_products(
    session_id: str,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[ProductResponse]:
    """Results so far. Safe to call mid-crawl: rows are committed as they land."""
    repo = CrawlRepository(db)
    if repo.get(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Crawl session {session_id} not found")
    return [ProductResponse.build(p) for p in repo.products(session_id, limit=limit)]


@router.post("/{session_id}/cancel")
def cancel_crawl(session_id: str, db: DbSession) -> CrawlSessionResponse:
    repo = CrawlRepository(db)
    session = repo.get(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Crawl session {session_id} not found")

    if not cancel_session(db, session):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is already {session.status}")

    db.refresh(session)
    return CrawlSessionResponse.build(session)
