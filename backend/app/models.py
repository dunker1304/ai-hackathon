from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text, func
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
    product_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("taxonomy.id"), nullable=True
    )
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
    product_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("taxonomy.id"), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrendPoint(Base):
    __tablename__ = "trends"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(Text)  # product_type_id or keyword
    entity_type: Mapped[str] = mapped_column(Text)  # "product_type" | "keyword"
    source: Mapped[str] = mapped_column(Text)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Float)


class Score(Base):
    """Precomputed opportunity score. `dims` is the single source of truth for
    per-dimension percentile value, raw value, explanation, and evidence
    [{metric, value, source, fetched_at}] — powers sliders, explain_score,
    evidence popovers, and the freshness requirement."""

    __tablename__ = "scores"

    product_type_id: Mapped[str] = mapped_column(
        ForeignKey("taxonomy.id", ondelete="CASCADE"), primary_key=True
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dims: Mapped[dict] = mapped_column(JSONB)
    total: Mapped[float] = mapped_column(Float)  # at DEFAULT_WEIGHTS
    fit: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(Text)  # recommend | conditional | not_recommend
