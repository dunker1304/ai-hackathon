"""Crawler tests: pagination, dedupe and stop conditions.

The client is faked so the whole aggregation flow runs offline and
deterministically -- no browser, no network, no Amazon.
"""

import pytest

from app.crawler.core.client.base import FetchResponse
from app.crawler.core.exceptions import BlockedError, ParseError
from app.crawler.marketplaces.amazon.crawler import AmazonCrawler
from app.crawler.marketplaces.amazon.url import SearchQuery, build_search_url

pytestmark = pytest.mark.asyncio


def make_serp(asins: list[str], *, has_next: bool = True) -> str:
    """Minimal SERP with the structure the parser depends on."""
    cards = "".join(
        f"""
        <div data-component-type="s-search-result" data-asin="{asin}">
            <a class="a-link-normal" href="javascript:void(0)">badge</a>
            <a class="a-link-normal" href="/p/dp/{asin}/ref=sr_1_1?crid=x"><h2>Item {asin}</h2></a>
        </div>
        """
        for asin in asins
    )
    pagination = (
        '<a class="s-pagination-next" href="/s?k=x&page=2">Next</a>'
        if has_next
        else '<span class="s-pagination-next s-pagination-disabled">Next</span>'
    )
    return f"<html><body>{cards}{pagination}</body></html>"


def asin_batch(start: int, count: int) -> list[str]:
    return [f"B{i:09d}" for i in range(start, start + count)]


class FakeSearchClient:
    """Stands in for AmazonSearchClient. Records the pages requested."""

    def __init__(self, pages: list[str], *, region: str = "us") -> None:
        self.pages = pages
        self.region = region
        self.requested: list[int] = []
        self.raise_on_page: dict[int, Exception] = {}

    async def fetch_search_page(self, query: SearchQuery, *, page: int = 1) -> FetchResponse:
        self.requested.append(page)
        if page in self.raise_on_page:
            raise self.raise_on_page[page]
        html = self.pages[min(page - 1, len(self.pages) - 1)]
        return FetchResponse(url=build_search_url(query, page=page), status=200, text=html)


def make_crawler(pages: list[str]) -> tuple[AmazonCrawler, FakeSearchClient]:
    client = FakeSearchClient(pages)
    return AmazonCrawler(client), client  # type: ignore[arg-type]


class TestPagination:
    async def test_follows_pages_until_no_next(self) -> None:
        pages = [
            make_serp(asin_batch(0, 10)),
            make_serp(asin_batch(10, 10)),
            make_serp(asin_batch(20, 10), has_next=False),
        ]
        crawler, client = make_crawler(pages)

        result = await crawler.collect_product_links("mug", max_products=500)

        assert result.count == 30
        assert result.pages_fetched == 3
        assert result.stopped_reason == "no_next_page"
        assert client.requested == [1, 2, 3]

    async def test_single_page_result(self) -> None:
        crawler, _ = make_crawler([make_serp(asin_batch(0, 5), has_next=False)])
        result = await crawler.collect_product_links("mug")
        assert result.count == 5
        assert result.pages_fetched == 1


class TestStopConditions:
    async def test_stops_at_max_products(self) -> None:
        pages = [make_serp(asin_batch(i * 48, 48)) for i in range(20)]
        crawler, client = make_crawler(pages)

        result = await crawler.collect_product_links("mug", max_products=100, max_pages=20)

        assert result.count == 100, "must truncate exactly at the limit"
        assert result.stopped_reason == "max_products"
        assert client.requested == [1, 2, 3], "must not fetch pages it does not need"

    async def test_stops_at_max_pages(self) -> None:
        pages = [make_serp(asin_batch(i * 10, 10)) for i in range(10)]
        crawler, client = make_crawler(pages)

        result = await crawler.collect_product_links("mug", max_products=500, max_pages=4)

        assert result.pages_fetched == 4
        assert result.stopped_reason == "max_pages"
        assert client.requested == [1, 2, 3, 4]

    async def test_stops_when_amazon_repeats_a_page(self) -> None:
        """Past the real end Amazon re-serves the last page instead of 404-ing;
        without this guard the crawler would loop to max_pages fetching nothing."""
        repeated = make_serp(asin_batch(0, 10))
        crawler, client = make_crawler([repeated, repeated, repeated])

        result = await crawler.collect_product_links("mug", max_products=500, max_pages=10)

        assert result.count == 10
        assert result.stopped_reason == "no_new_results"
        assert client.requested == [1, 2]

    async def test_default_ceiling_is_500(self) -> None:
        pages = [make_serp(asin_batch(i * 48, 48)) for i in range(30)]
        crawler, _ = make_crawler(pages)
        result = await crawler.collect_product_links("mug", max_pages=30)
        assert result.count == 500


class TestDedupe:
    async def test_asins_are_unique_across_pages(self) -> None:
        overlap = [
            make_serp(asin_batch(0, 10)),
            make_serp(asin_batch(5, 10)),  # 5 repeats
            make_serp(asin_batch(12, 10), has_next=False),
        ]
        crawler, _ = make_crawler(overlap)

        result = await crawler.collect_product_links("mug")
        asins = [link.asin for link in result.links]

        assert len(asins) == len(set(asins))
        assert result.count == 22

    async def test_positions_are_renumbered_after_dedupe(self) -> None:
        pages = [make_serp(asin_batch(0, 10)), make_serp(asin_batch(5, 10), has_next=False)]
        crawler, _ = make_crawler(pages)

        result = await crawler.collect_product_links("mug")

        assert [link.position for link in result.links] == list(range(1, result.count + 1))


class TestSponsored:
    def sponsored_page(self) -> str:
        return """
        <html><body>
        <div data-component-type="s-search-result" data-asin="B000000001">
            <div data-component-type="sp-sponsored-result"></div>
            <a class="a-link-normal" href="/p/dp/B000000001"><h2>Ad</h2></a>
        </div>
        <div data-component-type="s-search-result" data-asin="B000000002">
            <a class="a-link-normal" href="/p/dp/B000000002"><h2>Organic</h2></a>
        </div>
        <span class="s-pagination-next s-pagination-disabled">Next</span>
        </body></html>
        """

    async def test_excluded_by_default(self) -> None:
        """Paid placement is not demand; including it would distort the metrics."""
        crawler, _ = make_crawler([self.sponsored_page()])
        result = await crawler.collect_product_links("mug")
        assert [link.asin for link in result.links] == ["B000000002"]

    async def test_can_be_included(self) -> None:
        crawler, _ = make_crawler([self.sponsored_page()])
        result = await crawler.collect_product_links("mug", include_sponsored=True)
        assert result.count == 2


class TestErrorHandling:
    async def test_keeps_partial_results_when_a_later_page_is_blocked(self) -> None:
        crawler, client = make_crawler([make_serp(asin_batch(0, 10)), make_serp(asin_batch(10, 10))])
        client.raise_on_page[2] = BlockedError("captcha", url="x")

        result = await crawler.collect_product_links("mug", max_pages=5)

        assert result.count == 10, "page 1 data must not be thrown away"
        assert "error_on_page_2" in result.stopped_reason
        assert "BlockedError" in result.stopped_reason

    async def test_raises_when_the_first_page_fails(self) -> None:
        crawler, client = make_crawler([make_serp(asin_batch(0, 10))])
        client.raise_on_page[1] = BlockedError("captcha", url="x")

        with pytest.raises(BlockedError):
            await crawler.collect_product_links("mug")

    async def test_stop_on_error_propagates(self) -> None:
        crawler, client = make_crawler([make_serp(asin_batch(0, 10)), make_serp(asin_batch(10, 10))])
        client.raise_on_page[2] = ParseError("layout drift", url="x")

        with pytest.raises(ParseError):
            await crawler.collect_product_links("mug", stop_on_error=True)


class TestQueryPassthrough:
    async def test_accepts_a_prebuilt_query_with_filters(self) -> None:
        crawler, _client = make_crawler([make_serp(asin_batch(0, 5), has_next=False)])
        query = SearchQuery("mug", min_price=10, prime_only=True)

        result = await crawler.collect_product_links(query)

        assert result.keyword == "mug"
        assert result.count == 5

    async def test_links_carry_the_keyword(self) -> None:
        crawler, _ = make_crawler([make_serp(asin_batch(0, 3), has_next=False)])
        result = await crawler.collect_product_links("coffee mug")
        assert all(link.keyword == "coffee mug" for link in result.links)
