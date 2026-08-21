"""URL builder: the contract between a crawl intent and an Amazon URL."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.crawler.core.exceptions import CrawlerError
from app.crawler.core.types import SortBy, TimeWindow
from app.crawler.marketplaces.amazon.url import (
    SearchQuery,
    build_search_url,
    extract_asin,
    normalize_keyword,
    product_url,
)


def params_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


class TestNormalizeKeyword:
    def test_collapses_whitespace(self) -> None:
        assert normalize_keyword("  coffee   mug \n") == "coffee mug"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_keyword("   ")


class TestExtractAsin:
    @pytest.mark.parametrize(
        "href",
        [
            "/Design-Custom-Personalized/dp/B06ZXW1NM8/ref=sr_1_1?crid=3MB",
            "https://www.amazon.com/dp/B06ZXW1NM8",
            "/gp/product/B06ZXW1NM8?psc=1",
        ],
    )
    def test_extracts(self, href: str) -> None:
        assert extract_asin(href) == "B06ZXW1NM8"

    @pytest.mark.parametrize(
        "href",
        [
            "javascript:void(0)",
            "https://www.amazon.com/b/ref=s9_acss_bw_cg_sbp22c",
            "/s?k=coffee+mug",
            "",
        ],
    )
    def test_returns_none_for_non_product_links(self, href: str) -> None:
        assert extract_asin(href) is None


class TestBuildSearchUrl:
    def test_minimal(self) -> None:
        url = build_search_url(SearchQuery("coffee mug"))
        assert url.startswith("https://www.amazon.com/s?")
        assert params_of(url) == {"k": ["coffee mug"]}
        # Amazon's own links use "+" for spaces
        assert "k=coffee+mug" in url

    def test_page_one_omits_page_param(self) -> None:
        assert "page=" not in build_search_url(SearchQuery("mug"), page=1)

    def test_page_n(self) -> None:
        assert params_of(build_search_url(SearchQuery("mug"), page=3))["page"] == ["3"]

    def test_rejects_page_zero(self) -> None:
        with pytest.raises(ValueError, match="1-based"):
            build_search_url(SearchQuery("mug"), page=0)

    def test_sort(self) -> None:
        url = build_search_url(SearchQuery("mug", sort=SortBy.NEWEST))
        assert params_of(url)["s"] == ["date-desc-rank"]

    def test_price_range_is_in_cents(self) -> None:
        url = build_search_url(SearchQuery("mug", min_price=10, max_price=50))
        assert params_of(url)["rh"] == ["p_36:1000-5000"]

    def test_open_ended_price_range(self) -> None:
        url = build_search_url(SearchQuery("mug", min_price=25))
        assert params_of(url)["rh"] == ["p_36:2500-"]

    def test_refinements_are_comma_joined(self) -> None:
        url = build_search_url(SearchQuery("mug", min_price=10, min_rating=4, prime_only=True))
        rh = params_of(url)["rh"][0].split(",")
        assert rh == ["p_36:1000-", "p_72:2661618011", "p_85:2470955011"]

    def test_category_node(self) -> None:
        url = build_search_url(SearchQuery("mug", category_node="1055398"))
        assert params_of(url)["rh"] == ["n:1055398"]

    def test_department(self) -> None:
        assert params_of(build_search_url(SearchQuery("mug", department="kitchen")))["i"] == ["kitchen"]

    def test_filters_survive_pagination(self) -> None:
        query = SearchQuery("mug", sort=SortBy.NEWEST, min_rating=4)
        page3 = params_of(build_search_url(query, page=3))
        assert page3["s"] == ["date-desc-rank"]
        assert page3["rh"] == ["p_72:2661618011"]
        assert page3["page"] == ["3"]

    def test_is_deterministic(self) -> None:
        query = SearchQuery("mug", min_price=10, prime_only=True)
        assert build_search_url(query, page=2) == build_search_url(query, page=2)


class TestUnsupportedFilters:
    """Amazon has no generic time filter. Failing loudly beats silently
    returning unfiltered data that the AI layer would treat as filtered."""

    def test_unmapped_time_window_raises(self) -> None:
        with pytest.raises(CrawlerError, match="No date rnid mapped"):
            build_search_url(SearchQuery("mug", time_window=TimeWindow.D30))

    def test_unmapped_rating_raises(self) -> None:
        with pytest.raises(CrawlerError, match="No rnid mapped for min_rating"):
            build_search_url(SearchQuery("mug", min_rating=3))

    def test_unknown_region_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown region"):
            SearchQuery("mug", region="mars")

    def test_inverted_price_range_raises(self) -> None:
        with pytest.raises(ValueError, match="min_price must be <= max_price"):
            SearchQuery("mug", min_price=50, max_price=10)


class TestProductUrl:
    def test_is_canonical_and_tracking_free(self) -> None:
        assert product_url("B06ZXW1NM8") == "https://www.amazon.com/dp/B06ZXW1NM8"

    def test_region(self) -> None:
        assert product_url("B06ZXW1NM8", "uk") == "https://www.amazon.co.uk/dp/B06ZXW1NM8"
