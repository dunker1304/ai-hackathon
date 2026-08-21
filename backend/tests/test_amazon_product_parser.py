"""Detail-page parser tests against a real captured /dp page.

Ground truth for tests/fixtures/amazon_product_detail.html (B0721C21RJ,
Hanes EcoSmart sweatshirt, captured from a VN IP so prices are in VND):
rating 4.6, 141,921 reviews, "500+ bought in past month", 2 BSR entries,
45 attributes, parent ASIN B0BZR4ZCRW.
"""

import pytest

from selectolax.parser import HTMLParser

from app.crawler.core.exceptions import ParseError
from app.crawler.marketplaces.amazon.parsers.product import (
    clean,
    parse_best_seller_ranks,
    parse_bought_past_month,
    parse_bsr_from_tree,
    parse_price,
    parse_product_page,
    parse_rating,
    parse_review_count,
)

ASIN = "B0721C21RJ"
URL = f"https://www.amazon.com/dp/{ASIN}"


class TestClean:
    def test_strips_amazon_bidi_marks(self) -> None:
        """Amazon separates label and value with invisible LRM/RLM marks."""
        assert clean("Package Dimensions \u200f : \u200e 15.1 x 11.7 inches") == (
            "Package Dimensions : 15.1 x 11.7 inches"
        )

    def test_collapses_whitespace(self) -> None:
        assert clean("  a\n\n  b  ") == "a b"

    def test_empty_becomes_none(self) -> None:
        assert clean("   ") is None
        assert clean(None) is None


class TestParsePrice:
    @pytest.mark.parametrize(
        ("raw", "amount", "currency"),
        [
            ("$11.11", 11.11, "USD"),
            ("$1,234.56", 1234.56, "USD"),
            ("VND289,993", 289993.0, "VND"),
            ("£9.99", 9.99, "GBP"),
            ("€19.90", 19.90, "EUR"),
        ],
    )
    def test_parses_amount_and_currency(self, raw: str, amount: float, currency: str) -> None:
        assert parse_price(raw) == (amount, currency)

    def test_currency_is_always_reported(self) -> None:
        """Callers must be able to detect IP-driven currency switching; a bare
        float would make VND and USD indistinguishable."""
        _, currency = parse_price("VND289,993")
        assert currency == "VND"

    @pytest.mark.parametrize("raw", [None, "", "Currently unavailable", "Free"])
    def test_unparseable(self, raw: str | None) -> None:
        assert parse_price(raw) == (None, None)


class TestParseBoughtPastMonth:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("500+ bought in past month", 500),
            ("500+ boughtin past month", 500),  # rendered without the space
            ("2K+ bought in past month", 2000),
            ("1M+ bought in past month", 1_000_000),
            ("10,000+ bought in past month", 10000),
        ],
    )
    def test_parses(self, raw: str, expected: int) -> None:
        assert parse_bought_past_month(raw) == expected

    def test_absent_means_unknown_not_zero(self) -> None:
        """Amazon hides the widget for low-volume listings. Treating that as 0
        would invent demand data that does not exist."""
        assert parse_bought_past_month(None) is None
        assert parse_bought_past_month("") is None


class TestParseBestSellerRanks:
    def test_parses_both_broad_and_narrow_ranks(self) -> None:
        """Regression: the category link renders as "#2 inMen's Sweatshirts"
        with no space after "in", so `\\s+` dropped the narrow rank -- the one
        that actually carries the demand signal."""
        raw = "#203 in Clothing, Shoes & Jewelry (See Top 100 in Clothing, Shoes & Jewelry)#2 inMen's Sweatshirts"
        ranks = parse_best_seller_ranks(raw)

        assert [(r.rank, r.category) for r in ranks] == [
            (203, "Clothing, Shoes & Jewelry"),
            (2, "Men's Sweatshirts"),
        ]

    def test_skips_see_top_100_noise(self) -> None:
        ranks = parse_best_seller_ranks("#5 in Kitchen (See Top 100 in Kitchen)")
        assert [(r.rank, r.category) for r in ranks] == [(5, "Kitchen")]

    def test_strips_thousand_separators(self) -> None:
        assert parse_best_seller_ranks("#12,345 in Books")[0].rank == 12345

    def test_empty(self) -> None:
        assert parse_best_seller_ranks(None) == []

    def test_reads_both_entries_from_the_dom(self, amazon_detail_html: str) -> None:
        ranks = parse_bsr_from_tree(HTMLParser(amazon_detail_html))
        assert [(r.rank, r.category) for r in ranks] == [
            (203, "Clothing, Shoes & Jewelry"),
            (2, "Men's Sweatshirts"),
        ]


class TestScalarParsers:
    def test_rating(self) -> None:
        assert parse_rating("4.6 out of 5 stars") == pytest.approx(4.6)
        assert parse_rating("no stars here") is None

    def test_review_count_strips_parens_and_commas(self) -> None:
        assert parse_review_count("(141,921)") == 141921
        assert parse_review_count("141,921 ratings") == 141921


class TestParseProductPage:
    @pytest.fixture(scope="class")
    @classmethod
    def product(cls, amazon_detail_html: str):  # ruff: ignore[missing-return-type-class-method]
        return parse_product_page(amazon_detail_html, url=URL, keyword="sweatshirt", position=2)

    def test_core_fields(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        assert product.asin == ASIN
        assert product.title.startswith("Hanes Men's Sweatshirt")
        assert product.brand == "Hanes"
        assert product.rating == pytest.approx(4.6)
        assert product.review_count == 141921
        assert product.bought_past_month == 500
        assert product.availability == "In Stock"
        assert product.parse_confidence == pytest.approx(1.0)

    def test_carries_search_context(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        assert product.keyword == "sweatshirt"
        assert product.position == 2
        assert product.fetched_at is not None

    def test_url_is_canonical(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        assert product.url == f"https://www.amazon.com/dp/{ASIN}"

    def test_detects_parent_listing(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        """The canonical link points at the parent; keeping both lets the
        pipeline collapse variations of one product."""
        assert product.parent_asin == "B0BZR4ZCRW"
        assert product.variation_count == 227

    def test_attributes_exclude_the_size_chart(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        """A bare `tr th+td` sweep also matches the apparel size chart
        (XS=30-32, S=34-36, ...), which is not product metadata."""
        assert product.attributes["Brand Name"] == "Hanes"
        assert product.attributes["Color"] == "Stonewashed Green"
        assert "XS" not in product.attributes
        assert "6XL" not in product.attributes

    def test_bullets_and_categories(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        assert len(product.bullets) >= 3
        assert product.categories[0] == "Clothing, Shoes & Jewelry"

    def test_image_is_full_resolution(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        """`src` is a thumbnail; `data-old-hires` is the original."""
        assert product.image_url.endswith(".jpg")
        assert "_SL1500_" in product.image_url

    def test_primary_rank_is_the_narrowest(self, product) -> None:  # ruff: ignore[missing-type-function-argument]
        assert product.primary_rank.rank == 2
        assert product.primary_rank.category == "Men's Sweatshirts"


class TestCurrencyGuard:
    def test_reports_the_currency_it_actually_found(self, amazon_detail_html: str) -> None:
        """The fixture was captured from a VN IP. The parser must surface VND
        rather than silently returning a number that looks like dollars."""
        product = parse_product_page(amazon_detail_html, url=URL)
        assert product.currency == "VND"
        assert product.price == pytest.approx(289993.0)

    def test_warns_on_mismatch(self, amazon_detail_html: str, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            parse_product_page(amazon_detail_html, url=URL, expected_currency="USD")
        assert "priced in VND" in caplog.text

    def test_no_warning_when_expectation_matches(
        self, amazon_detail_html: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            parse_product_page(amazon_detail_html, url=URL, expected_currency="VND")
        assert "priced in" not in caplog.text


class TestUnshippable:
    """Amazon removes the buybox when the delivery address cannot receive the
    item, so the price element is absent from the DOM entirely. That is a
    location problem, not a parsing one, and the two must stay distinguishable."""

    UNSHIPPABLE_HTML = """
    <html><body>
        <span id="productTitle">Owala Tumbler</span>
        <input id="ASIN" value="B000000001">
        <div id="outOfStock">This item cannot be shipped to your selected
            delivery location. Please choose a different delivery location.</div>
    </body></html>
    """

    def test_flags_the_page(self) -> None:
        product = parse_product_page(self.UNSHIPPABLE_HTML, url="https://www.amazon.com/dp/B000000001")
        assert product.unshippable is True
        assert product.price is None

    def test_still_returns_the_record(self) -> None:
        """Title, rating and BSR are all still present, so the row is worth
        keeping -- only the price is unknown."""
        product = parse_product_page(self.UNSHIPPABLE_HTML, url="https://www.amazon.com/dp/B000000001")
        assert product.title == "Owala Tumbler"

    def test_revenue_is_none_not_zero(self) -> None:
        product = parse_product_page(self.UNSHIPPABLE_HTML, url="https://www.amazon.com/dp/B000000001")
        assert product.estimated_monthly_revenue is None

    def test_priced_page_is_not_flagged(self) -> None:
        html = """
        <html><body>
            <span id="productTitle">Thing</span>
            <input id="ASIN" value="B000000001">
            <div id="corePrice_feature_div"><span class="a-offscreen">$10.00</span></div>
        </body></html>
        """
        product = parse_product_page(html, url="https://www.amazon.com/dp/B000000001")
        assert product.unshippable is False

    def test_missing_price_without_the_marker_is_not_flagged(self) -> None:
        """A genuinely unavailable product is a different failure from one we
        simply were not shown."""
        html = """
        <html><body>
            <span id="productTitle">Thing</span>
            <input id="ASIN" value="B000000001">
            <div id="availability"><span>Currently unavailable.</span></div>
        </body></html>
        """
        product = parse_product_page(html, url="https://www.amazon.com/dp/B000000001")
        assert product.unshippable is False
        assert product.price is None


class TestRevenueEstimate:
    def test_multiplies_price_by_volume(self, amazon_detail_html: str) -> None:
        product = parse_product_page(amazon_detail_html, url=URL)
        assert product.estimated_monthly_revenue == pytest.approx(289993.0 * 500)

    def test_none_when_volume_is_unknown(self) -> None:
        """No `bought in past month` means unknown, so revenue must be None
        rather than 0 -- a fake zero would drag down every aggregate."""
        html = """
        <html><body>
            <span id="productTitle">Thing</span>
            <input id="ASIN" value="B000000001">
            <div id="corePrice_feature_div"><span class="a-offscreen">$10.00</span></div>
        </body></html>
        """
        product = parse_product_page(html, url="https://www.amazon.com/dp/B000000001")
        assert product.bought_past_month is None
        assert product.estimated_monthly_revenue is None


class TestFailureModes:
    def test_interstitial_raises(self) -> None:
        """An Akamai challenge has neither an ASIN nor a title."""
        with pytest.raises(ParseError, match="Could not resolve an ASIN"):
            parse_product_page("<html><body>bm-verify</body></html>", url="https://www.amazon.com/s?k=x")

    def test_interstitial_on_a_dp_url_still_raises(self) -> None:
        """The URL carries an ASIN, so the record would look valid; the missing
        title is what proves the page never rendered."""
        with pytest.raises(ParseError, match="No #productTitle"):
            parse_product_page("<html><body>bm-verify</body></html>", url=URL)

    def test_missing_title_raises(self) -> None:
        html = '<html><body><input id="ASIN" value="B000000001"></body></html>'
        with pytest.raises(ParseError, match="No #productTitle"):
            parse_product_page(html, url="https://www.amazon.com/dp/B000000001")

    def test_partial_page_still_parses_with_low_confidence(self) -> None:
        """A record missing price/rating is still worth keeping; the confidence
        score lets the pipeline decide."""
        html = """
        <html><body>
            <span id="productTitle">Some Product</span>
            <input id="ASIN" value="B000000001">
        </body></html>
        """
        product = parse_product_page(html, url="https://www.amazon.com/dp/B000000001")
        assert product.title == "Some Product"
        assert product.price is None
        assert product.parse_confidence == pytest.approx(0.2)

    def test_dom_asin_wins_over_url(self) -> None:
        """/dp/<child> can redirect to the parent; the DOM describes what was
        actually served."""
        html = """
        <html><body>
            <span id="productTitle">Thing</span>
            <input id="ASIN" value="B0PARENT01">
        </body></html>
        """
        product = parse_product_page(html, url="https://www.amazon.com/dp/B0CHILD001")
        assert product.asin == "B0PARENT01"
