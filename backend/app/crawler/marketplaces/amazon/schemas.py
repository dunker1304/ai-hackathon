"""Amazon-specific pydantic schemas (AmazonSearchItem, AmazonProduct, AmazonBestSellerItem, AmazonKeyword) before
normalization."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.crawler.marketplaces.amazon.url import product_url


class ProductLink(BaseModel):
    """A product discovered on a SERP. Phase-1 output: enough to queue a detail
    crawl, nothing more."""

    asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    url: str
    title: str | None = None
    position: int | None = None  # 1-based rank across the whole crawl
    page: int | None = None
    sponsored: bool = False
    keyword: str | None = None

    @classmethod
    def from_asin(cls, asin: str, *, region: str = "us", **kwargs: object) -> ProductLink:
        return cls(asin=asin, url=product_url(asin, region), **kwargs)  # type: ignore[arg-type]


class BestSellerRank(BaseModel):
    """One "#203 in Clothing, Shoes & Jewelry" entry. A product usually has a
    broad rank plus one or more narrow category ranks; the narrow ones are the
    useful demand signal."""

    rank: int
    category: str


class AmazonProduct(BaseModel):
    """A parsed /dp page.

    Every field except `asin` is optional: Amazon renders wildly different
    layouts per category, and a partially-filled record still carries signal.
    `parse_confidence` reports how much of the core set was recovered so the
    pipeline can drop hollow rows instead of treating them as real zeroes.
    """

    asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    url: str
    title: str | None = None
    brand: str | None = None

    price: float | None = None
    list_price: float | None = None
    currency: str | None = None

    rating: float | None = None
    review_count: int | None = None
    #: Parsed from "500+ bought in past month" -- Amazon's only first-party
    #: 30-day volume figure, and a lower bound (500+ means >= 500).
    bought_past_month: int | None = None

    availability: str | None = None
    #: True when Amazon suppressed the buybox because the delivery address
    #: cannot receive the item. `price is None` then means "not shown to us",
    #: not "no price" -- the two must not be conflated downstream.
    unshippable: bool = False
    best_seller_ranks: list[BestSellerRank] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    image_url: str | None = None

    #: Set when the /dp page redirects to a parent listing; the crawled ASIN and
    #: the canonical one then differ.
    parent_asin: str | None = None
    variation_count: int | None = None

    keyword: str | None = None
    position: int | None = None
    fetched_at: datetime | None = None
    parse_confidence: float = 0.0

    @property
    def estimated_monthly_revenue(self) -> float | None:
        """`bought_past_month` x price. A floor, not an estimate: Amazon buckets
        the count ("500+") and hides it entirely below a threshold."""
        if self.price is None or self.bought_past_month is None:
            return None
        return round(self.price * self.bought_past_month, 2)

    @property
    def primary_rank(self) -> BestSellerRank | None:
        """The narrowest (highest-numbered category, lowest rank) BSR entry."""
        return min(self.best_seller_ranks, key=lambda r: r.rank) if self.best_seller_ranks else None


class ProductBatch(BaseModel):
    """Result of crawling many detail pages."""

    products: list[AmazonProduct] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)  # asin -> error
    elapsed_seconds: float = 0.0

    @property
    def count(self) -> int:
        return len(self.products)

    @property
    def success_rate(self) -> float:
        total = len(self.products) + len(self.failed)
        return len(self.products) / total if total else 0.0


class SearchPage(BaseModel):
    """One parsed SERP."""

    url: str
    page: int
    links: list[ProductLink] = Field(default_factory=list)
    next_page_url: str | None = None
    total_result_count: int | None = None

    @property
    def has_next(self) -> bool:
        return self.next_page_url is not None


class LinkCollection(BaseModel):
    """Aggregated result of `AmazonCrawler.collect_product_links`."""

    keyword: str
    links: list[ProductLink] = Field(default_factory=list)
    pages_fetched: int = 0
    stopped_reason: str = ""

    @property
    def count(self) -> int:
        return len(self.links)
