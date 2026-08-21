import operator

from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1536  # must match scripts/init_db.sql and text-embedding-3-small


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Product Opportunity Hub ---


class ProductType(Base):
    """One node of the Printway catalog taxonomy. `fit` (0-100) is precomputed
    at seed time from difficulty + margins; `seasonality` holds the demand
    profile: {"monthly": [12 floats], "peak_month": 12, "peak_label": "Christmas"}."""

    __tablename__ = "taxonomy"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. "acrylic-ornament"
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    material: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[int] = mapped_column(Integer)  # 1 (easy) - 5 (hard)
    margin_min: Mapped[float] = mapped_column(Float)
    margin_max: Mapped[float] = mapped_column(Float)
    personalization_friendly: Mapped[bool] = mapped_column(Boolean, default=True)
    fit: Mapped[int] = mapped_column(Integer, default=0)
    seasonality: Mapped[dict] = mapped_column(JSONB, default=dict)


class TaxonomyAlias(Base):
    """Seller-style alias for a taxonomy node ("Custom Pet Photo Xmas Bauble").
    Normalization retrieves by max cosine similarity over ALL aliases."""

    __tablename__ = "taxonomy_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_type_id: Mapped[str] = mapped_column(ForeignKey("taxonomy.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text)  # "etsy" | "amazon"
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float)
    favorites: Mapped[int] = mapped_column(Integer, default=0)
    est_sales: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    shop: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    product_type_id: Mapped[str | None] = mapped_column(ForeignKey("taxonomy.id"), nullable=True)
    norm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    volume: Mapped[int] = mapped_column(Integer)
    competition: Mapped[float] = mapped_column(Float)  # 0-1
    cpc: Mapped[float] = mapped_column(Float)
    trend_30d: Mapped[float] = mapped_column(Float)  # % change over 30 days
    product_type_id: Mapped[str | None] = mapped_column(ForeignKey("taxonomy.id"), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrendPoint(Base):
    __tablename__ = "trends"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(Text)  # product_type_id or keyword
    entity_type: Mapped[str] = mapped_column(Text)  # "product_type" | "keyword"
    source: Mapped[str] = mapped_column(Text)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Float)


# --- Crawl sessions -------------------------------------------------------


class CrawlStatus(StrEnum):
    """Lifecycle of one crawl job. Stored as text so a stuck value can be
    inspected and fixed with plain SQL."""

    PENDING = "pending"  # queued, no worker yet
    DISCOVERING = "discovering"  # phase 1: walking SERPs
    FETCHING = "fetching"  # phase 2: detail pages
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {CrawlStatus.COMPLETED, CrawlStatus.FAILED, CrawlStatus.CANCELLED}


class CrawlSession(Base):
    """One user request: keywords + region + limit, and everything needed to
    render a progress page while the worker runs.

    Progress is written to Postgres rather than kept only in Celery's result
    backend so the status survives a worker restart and can be joined against
    the rows it produced.
    """

    __tablename__ = "crawl_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # uuid4 hex
    marketplace: Mapped[str] = mapped_column(Text, default="amazon")

    # --- request ---
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    region: Mapped[str] = mapped_column(Text, default="us")
    location: Mapped[str | None] = mapped_column(Text, nullable=True)  # delivery ZIP
    max_products: Mapped[int] = mapped_column(Integer, default=100)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)  # sort, price range, ...

    # --- execution ---
    status: Mapped[str] = mapped_column(Text, default=CrawlStatus.PENDING, index=True)
    task_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # celery id, for revoke
    phase_detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # human-readable step

    links_found: Mapped[int] = mapped_column(Integer, default=0)
    products_done: Mapped[int] = mapped_column(Integer, default=0)
    products_failed: Mapped[int] = mapped_column(Integer, default=0)
    #: Denominator for the progress bar. Only known after phase 1, hence nullable.
    products_total: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Data-quality report from the detail pass: coverage, currencies,
    #: unshippable count. Mirrors scripts/crawl_amazon_e2e.py.
    quality: Mapped[dict] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def progress(self) -> float:
        """0.0-1.0 for the progress bar. Phase 1 has no denominator, so it is
        reported as a flat 10% rather than a fake percentage."""
        if self.status == CrawlStatus.COMPLETED:
            return 1.0
        if not self.products_total:
            return 0.1 if self.status == CrawlStatus.DISCOVERING else 0.0
        done = self.products_done + self.products_failed
        return round(min(0.1 + 0.9 * done / self.products_total, 1.0), 3)


class CrawlKeyword(Base):
    """Per-keyword outcome of phase 1. Mirrors one entry of
    `result/list_data.json.keywords[]`."""

    __tablename__ = "crawl_keywords"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("crawl_sessions.id", ondelete="CASCADE"), index=True)
    keyword: Mapped[str] = mapped_column(Text)
    links_found: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    #: max_products | no_next_page | no_new_results | max_pages | error_on_page_N
    stopped_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrawlProduct(Base):
    """A crawled marketplace product, raw. One row per (session, external_id).

    Kept separate from `listings`: this is the unnormalized crawl output, and
    `pipelines/normalize.py` is what turns it into taxonomy-mapped listings.
    Overwriting the raw rows would make a normalization bug unrecoverable
    without re-crawling.
    """

    __tablename__ = "crawl_products"
    __table_args__ = (
        UniqueConstraint("session_id", "external_id", name="uq_crawl_products_session_external"),
        Index("ix_crawl_products_marketplace_external", "marketplace", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("crawl_sessions.id", ondelete="CASCADE"), index=True)
    marketplace: Mapped[str] = mapped_column(Text, default="amazon")
    #: ASIN on Amazon, product_id on TikTok Shop.
    external_id: Mapped[str] = mapped_column(Text, index=True)
    url: Mapped[str] = mapped_column(Text)

    # --- discovery (phase 1) ---
    keyword: Mapped[str | None] = mapped_column(Text, index=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sponsored: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- detail (phase 2); all nullable, a link-only row is still useful ---
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    list_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Never read `price` without this: Amazon quotes the currency of the exit
    #: IP, so the same product can arrive as USD 11.11 or VND 289,993.
    currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: From "500+ bought in past month". A floor, and NULL means unknown --
    #: Amazon hides the widget for low-volume listings. Never coalesce to 0.
    bought_past_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: True when Amazon suppressed the buybox for the configured delivery
    #: address. `price IS NULL AND unshippable` means "not shown to us",
    #: which is different from "has no price".
    unshippable: Mapped[bool] = mapped_column(Boolean, default=False)

    best_seller_ranks: Mapped[list] = mapped_column(JSONB, default=list)  # [{rank, category}]
    categories: Mapped[list] = mapped_column(JSONB, default=list)
    bullets: Mapped[list] = mapped_column(JSONB, default=list)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_asin: Mapped[str | None] = mapped_column(Text, nullable=True)
    variation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: 0.0-1.0 share of core fields recovered; lets the pipeline drop hollow rows.
    parse_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    detail_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def primary_rank(self) -> dict | None:
        """Narrowest BSR entry -- the one that carries the demand signal."""
        return min(self.best_seller_ranks, key=operator.itemgetter("rank")) if self.best_seller_ranks else None

    @property
    def estimated_monthly_revenue(self) -> float | None:
        if self.price is None or self.bought_past_month is None:
            return None
        return round(self.price * self.bought_past_month, 2)


class Score(Base):
    """Precomputed opportunity score. `dims` is the single source of truth for
    per-dimension percentile value, raw value, explanation, and evidence
    [{metric, value, source, fetched_at}] — powers sliders, explain_score,
    evidence popovers, and the freshness requirement."""

    __tablename__ = "scores"

    product_type_id: Mapped[str] = mapped_column(ForeignKey("taxonomy.id", ondelete="CASCADE"), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dims: Mapped[dict] = mapped_column(JSONB)
    total: Mapped[float] = mapped_column(Float)  # at DEFAULT_WEIGHTS
    fit: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(Text)  # recommend | conditional | not_recommend
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
