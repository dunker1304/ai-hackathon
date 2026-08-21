"""Shared type aliases & enums: Marketplace, TimeWindow (7d/30d/90d/1y), SortBy, Currency, JSONDict."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeAlias

JSONDict: TypeAlias = dict[str, Any]
Headers: TypeAlias = dict[str, str]
QueryParams: TypeAlias = dict[str, str | int | float | bool | None]
Cookies: TypeAlias = list[dict[str, Any]]


class Marketplace(StrEnum):
    AMAZON = "amazon"
    TIKTOKSHOP = "tiktokshop"


class TimeWindow(StrEnum):
    """Analytics window. Not every marketplace exposes all of them natively;
    `pipelines.metrics` may derive missing windows from snapshots."""

    D7 = "7d"
    D30 = "30d"
    D90 = "90d"
    Y1 = "1y"

    @property
    def days(self) -> int:
        return {"7d": 7, "30d": 30, "90d": 90, "1y": 365}[self.value]


class SortBy(StrEnum):
    RELEVANCE = "relevance"
    BEST_SELLER = "best_seller"
    NEWEST = "newest"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    RATING = "rating"


class Currency(StrEnum):
    USD = "USD"
    VND = "VND"
    EUR = "EUR"
    GBP = "GBP"


class ResourceKind(StrEnum):
    """What a client call is fetching. Used for rate-limit buckets & metrics."""

    SEARCH = "search"
    PRODUCT = "product"
    BESTSELLER = "bestseller"
    SHOP = "shop"
    KEYWORD = "keyword"


BrowserOS: TypeAlias = Literal["windows", "macos", "linux"]
WaitUntil: TypeAlias = Literal["commit", "domcontentloaded", "load", "networkidle"]
