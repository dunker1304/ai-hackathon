"""Parser tests against a real captured SERP.

Numbers below are ground truth for tests/fixtures/amazon_search_page1.html:
48 organic result cards, 0 sponsored, 3 pages, totalResultCount 116.
"""

import pytest

from selectolax.parser import HTMLParser

from app.crawler.core.exceptions import ParseError
from app.crawler.marketplaces.amazon.parsers.search import (
    card_asin,
    card_is_sponsored,
    find_next_page_url,
    find_result_cards,
    parse_search_page,
    parse_total_results,
)

EXPECTED_CARDS = 48
FIRST_ASIN = "B06ZXW1NM8"
SERP_URL = "https://www.amazon.com/s?k=amazon+personalized+sweatshirts"


class TestFindResultCards:
    def test_finds_all_cards(self, amazon_serp_html: str) -> None:
        assert len(find_result_cards(HTMLParser(amazon_serp_html))) == EXPECTED_CARDS

    def test_falls_back_across_layouts(self) -> None:
        """Amazon serves several grid layouts; the parser must not depend on one."""
        html = """
        <div class="a-section a-spacing-base desktop-grid-content-view">
            <a class="a-link-normal" href="/x/dp/B000000001/ref=sr_1_1">t</a>
        </div>
        """
        assert len(find_result_cards(HTMLParser(html))) == 1


class TestCardAsin:
    def test_picks_the_single_product_asin_per_card(self, amazon_serp_html: str) -> None:
        """Each card holds ~5 `a.a-link-normal` (title, image, reviews, a
        javascript:void(0) badge, brand ads). Only one real ASIN must survive."""
        cards = find_result_cards(HTMLParser(amazon_serp_html))
        asins = [card_asin(c) for c in cards]

        assert all(a is not None for a in asins)
        assert len(set(asins)) == EXPECTED_CARDS, "ASINs must be unique per card"
        assert asins[0] == FIRST_ASIN

    def test_ignores_javascript_and_brand_links(self) -> None:
        html = """
        <div data-component-type="s-search-result">
            <a class="a-link-normal" href="javascript:void(0)">badge</a>
            <a class="a-link-normal" href="https://www.amazon.com/b/ref=s9_acss">brand ad</a>
            <a class="a-link-normal" href="/Some-Product/dp/B0CNK6BH5D/ref=sr_1_2?crid=x">title</a>
        </div>
        """
        card = find_result_cards(HTMLParser(html))[0]
        assert card_asin(card) == "B0CNK6BH5D"

    def test_falls_back_to_data_asin(self) -> None:
        html = '<div data-component-type="s-search-result" data-asin="B0ABCDEFGH"></div>'
        assert card_asin(find_result_cards(HTMLParser(html))[0]) == "B0ABCDEFGH"


class TestSponsoredDetection:
    def test_organic_cards_are_not_flagged(self, amazon_serp_html: str) -> None:
        """Regression: every card embeds a JSON blob containing the substring
        "Sponsored" (`isSponsored":""`, `searchProductType":"ORGANIC"`).
        Naive substring matching flagged 27/48 organic cards as ads."""
        cards = find_result_cards(HTMLParser(amazon_serp_html))
        assert sum(card_is_sponsored(c) for c in cards) == 0

    def test_detects_real_ad_component(self) -> None:
        html = """
        <div data-component-type="s-search-result">
            <div data-component-type="sp-sponsored-result"></div>
            <a class="a-link-normal" href="/x/dp/B000000001">t</a>
        </div>
        """
        assert card_is_sponsored(find_result_cards(HTMLParser(html))[0]) is True

    def test_detects_non_organic_product_type(self) -> None:
        html = """
        <div data-component-type="s-search-result"
             data-json='{"searchProductType":"SPONSORED"}'>
            <a class="a-link-normal" href="/x/dp/B000000001">t</a>
        </div>
        """
        assert card_is_sponsored(find_result_cards(HTMLParser(html))[0]) is True


class TestPagination:
    def test_finds_next_page(self, amazon_serp_html: str) -> None:
        url = find_next_page_url(HTMLParser(amazon_serp_html), base_url="https://www.amazon.com")
        assert url is not None
        assert url.startswith("https://www.amazon.com/s?")
        assert "page=2" in url

    def test_disabled_next_is_a_span_without_href(self) -> None:
        """On the last page Amazon swaps the <a> for a <span>; absence of an
        href is the stop condition."""
        html = '<span class="s-pagination-item s-pagination-next s-pagination-disabled">Next</span>'
        assert find_next_page_url(HTMLParser(html), base_url="https://www.amazon.com") is None

    def test_aria_disabled_next(self) -> None:
        html = '<a class="s-pagination-next" aria-disabled="true" href="/s?page=9">Next</a>'
        assert find_next_page_url(HTMLParser(html), base_url="https://www.amazon.com") is None

    def test_no_pagination_at_all(self) -> None:
        assert find_next_page_url(HTMLParser("<div></div>"), base_url="https://www.amazon.com") is None


class TestParseSearchPage:
    def test_extracts_every_product(self, amazon_serp_html: str) -> None:
        page = parse_search_page(amazon_serp_html, url=SERP_URL, page=1, keyword="personalized sweatshirts")

        assert len(page.links) == EXPECTED_CARDS
        assert len({link.asin for link in page.links}) == EXPECTED_CARDS
        assert page.has_next
        assert page.total_result_count == 116

    def test_urls_are_canonical(self, amazon_serp_html: str) -> None:
        """Raw hrefs carry ~400 bytes of tracking (ref=, dib=, qid=) that breaks
        dedupe across pages and runs."""
        page = parse_search_page(amazon_serp_html, url=SERP_URL)
        for link in page.links:
            assert link.url == f"https://www.amazon.com/dp/{link.asin}"
            assert "ref=" not in link.url
            assert "?" not in link.url

    def test_positions_are_sequential(self, amazon_serp_html: str) -> None:
        page = parse_search_page(amazon_serp_html, url=SERP_URL)
        assert [link.position for link in page.links] == list(range(1, EXPECTED_CARDS + 1))

    def test_start_position_offsets_for_pagination(self, amazon_serp_html: str) -> None:
        page = parse_search_page(amazon_serp_html, url=SERP_URL, page=2, start_position=49)
        assert page.links[0].position == 49

    def test_carries_keyword_and_page(self, amazon_serp_html: str) -> None:
        page = parse_search_page(amazon_serp_html, url=SERP_URL, page=2, keyword="mug")
        assert all(link.keyword == "mug" and link.page == 2 for link in page.links)

    def test_titles_are_populated(self, amazon_serp_html: str) -> None:
        page = parse_search_page(amazon_serp_html, url=SERP_URL)
        assert sum(1 for link in page.links if link.title) >= EXPECTED_CARDS * 0.9

    def test_layout_drift_raises_instead_of_returning_empty(self) -> None:
        """A silent empty list would look like "keyword has no products" and
        poison the analytics; a drifted selector must be loud."""
        with pytest.raises(ParseError, match="No result cards matched"):
            parse_search_page("<html><body><div>unexpected</div></body></html>", url=SERP_URL)

    def test_genuine_no_results_page_is_empty_not_an_error(self) -> None:
        html = '<html><body><div data-cy="no-results-header">No results for xyz</div></body></html>'
        page = parse_search_page(html, url=SERP_URL, keyword="xyz")
        assert page.links == []
        assert page.has_next is False


class TestParseTotalResults:
    def test_reads_total(self, amazon_serp_html: str) -> None:
        assert parse_total_results(amazon_serp_html) == 116

    def test_absent(self) -> None:
        assert parse_total_results("<html></html>") is None
