"""Search-URL builder.

`SearchQuery` is the single place where a crawl intent (keyword + filters) turns
into an Amazon URL, so pagination can rebuild a clean URL for page N instead of
following Amazon's tracking-laden `next` href.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urlencode

from app.crawler.core.exceptions import CrawlerError
from app.crawler.marketplaces.amazon import constants as const

if TYPE_CHECKING:
    from app.crawler.core.types import SortBy, TimeWindow

ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)")


def normalize_keyword(keyword: str) -> str:
    """Collapse whitespace; Amazon treats `+` as the separator in `k`."""
    cleaned = " ".join(keyword.split())
    if not cleaned:
        raise ValueError("keyword must not be empty")
    return cleaned


def extract_asin(href: str) -> str | None:
    """Pull the ASIN out of any Amazon product URL, absolute or relative."""
    match = ASIN_RE.search(href)
    return match.group(1) if match else None


def product_url(asin: str, region: str = const.DEFAULT_REGION) -> str:
    """Canonical, tracking-free product URL. Stable dedupe key across runs."""
    return const.BASE_URLS[region] + const.PRODUCT_PATH.format(asin=asin)


@dataclass(slots=True)
class SearchQuery:
    """A search intent. Convert to a URL with `build_search_url`.

        q = SearchQuery("personalized sweatshirt", sort=SortBy.NEWEST, min_rating=4)
        url = build_search_url(q, page=2)

    Filters are additive; unsupported combinations are rejected loudly rather
    than silently dropped, because a missing filter corrupts the analytics
    downstream.
    """

    keyword: str
    region: str = const.DEFAULT_REGION
    sort: SortBy | None = None
    department: str | None = None  # e.g. "fashion", "kitchen"
    category_node: str | None = None  # browse node id, narrows the result set
    min_price: float | None = None  # in the storefront currency
    max_price: float | None = None
    min_rating: int | None = None  # only 4 is mapped today
    prime_only: bool = False
    time_window: TimeWindow | None = None  # "new arrivals" rail
    extra_params: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.keyword = normalize_keyword(self.keyword)
        if self.region not in const.BASE_URLS:
            raise ValueError(f"Unknown region {self.region!r}; known: {sorted(const.BASE_URLS)}")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price must be <= max_price")

    @property
    def base_url(self) -> str:
        return const.BASE_URLS[self.region]

    def refinements(self) -> list[str]:
        """Build the `rh` clauses. Amazon joins them with commas."""
        parts: list[str] = []

        if self.category_node:
            parts.append(f"{const.RH_CATEGORY}:{self.category_node}")

        if self.min_price is not None or self.max_price is not None:
            lo = int((self.min_price or 0) * 100)
            hi = int(self.max_price * 100) if self.max_price is not None else ""
            parts.append(f"{const.RH_PRICE}:{lo}-{hi}")

        if self.min_rating is not None:
            rnid = const.RATING_RNIDS.get(self.min_rating)
            if rnid is None:
                raise CrawlerError(
                    f"No rnid mapped for min_rating={self.min_rating}; "
                    f"known: {sorted(const.RATING_RNIDS)} (see amazon/PLAN.md)"
                )
            parts.append(f"{const.RH_RATING}:{rnid}")

        if self.prime_only:
            parts.append(f"{const.RH_PRIME}:{const.PRIME_RNID}")

        if self.time_window is not None:
            parts.append(self._date_refinement())

        return parts

    def _date_refinement(self) -> str:
        """Map a TimeWindow onto Amazon's department-specific "new arrivals" rnid.

        The rnid table is populated by hand (see PLAN.md); until an entry exists
        this raises so a silently-unfiltered crawl never reaches the analytics.
        """
        assert self.time_window is not None
        department = self.department or "all"
        by_dept = const.DATE_FIRST_AVAILABLE_RNIDS.get(department)
        if not by_dept or self.time_window not in by_dept:
            raise CrawlerError(
                f"No date rnid mapped for department={department!r} "
                f"window={self.time_window.value}. Amazon has no generic "
                f"'last N days' filter; populate DATE_FIRST_AVAILABLE_RNIDS "
                f"(see amazon/PLAN.md) or drop time_window."
            )
        return f"{const.RH_DATE_FIRST_AVAILABLE}:{by_dept[self.time_window]}"

    def params(self, *, page: int = 1) -> dict[str, str]:
        if page < 1:
            raise ValueError("page is 1-based")

        params: dict[str, str] = {const.PARAM_KEYWORD: self.keyword}

        if page > 1:
            params[const.PARAM_PAGE] = str(page)
        if self.sort is not None:
            sort_value = const.SORT_PARAMS.get(self.sort.value)
            if sort_value is None:
                raise CrawlerError(f"Sort {self.sort.value!r} is not supported on Amazon")
            params[const.PARAM_SORT] = sort_value
        if self.department:
            params[const.PARAM_DEPARTMENT] = self.department

        refinements = self.refinements()
        if refinements:
            params[const.PARAM_REFINEMENT] = ",".join(refinements)

        params.update(self.extra_params)
        return params

    def cache_key(self) -> str:
        """Stable identity for logging / dedupe across pages."""
        return f"{self.region}:{self.keyword}:{sorted(self.params().items())}"


def build_search_url(query: SearchQuery, *, page: int = 1) -> str:
    """Render a clean, reproducible SERP URL.

    `quote_plus` keeps spaces as `+`, matching Amazon's own links.
    """
    encoded = urlencode(query.params(page=page), quote_via=quote_plus)
    return f"{query.base_url}{const.SEARCH_PATH}?{encoded}"
