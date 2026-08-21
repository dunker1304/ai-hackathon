"""Crawl session model behaviour and request validation.

No database: these cover the pure logic that the progress page and the API
depend on.
"""

import pytest

from app.crawl.quality import quality_report
from app.crawl.service import MAX_KEYWORDS, MAX_PRODUCTS, validate_request
from app.crawler.marketplaces.amazon.schemas import AmazonProduct, BestSellerRank
from app.models import CrawlProduct, CrawlSession, CrawlStatus


def make_session(**kwargs) -> CrawlSession:
    defaults = {
        "id": "abc",
        "status": CrawlStatus.PENDING,
        "products_done": 0,
        "products_failed": 0,
        "products_total": None,
    }
    return CrawlSession(**(defaults | kwargs))


def make_product(**kwargs) -> AmazonProduct:
    defaults = {"asin": "B000000001", "url": "https://www.amazon.com/dp/B000000001", "title": "Thing"}
    return AmazonProduct(**(defaults | kwargs))


class TestCrawlStatus:
    @pytest.mark.parametrize("value", [CrawlStatus.COMPLETED, CrawlStatus.FAILED, CrawlStatus.CANCELLED])
    def test_terminal(self, value: CrawlStatus) -> None:
        assert value.is_terminal

    @pytest.mark.parametrize("value", [CrawlStatus.PENDING, CrawlStatus.DISCOVERING, CrawlStatus.FETCHING])
    def test_not_terminal(self, value: CrawlStatus) -> None:
        assert not value.is_terminal


class TestProgress:
    def test_pending_is_zero(self) -> None:
        assert make_session().progress == pytest.approx(0.0)

    def test_discovery_has_no_denominator(self) -> None:
        """Phase 1 cannot know how many products exist, so it reports a flat
        10% instead of inventing a percentage."""
        assert make_session(status=CrawlStatus.DISCOVERING).progress == pytest.approx(0.1)

    def test_fetching_scales_between_10_and_100(self) -> None:
        session = make_session(status=CrawlStatus.FETCHING, products_total=100, products_done=50)
        assert session.progress == pytest.approx(0.55)

    def test_failures_count_towards_progress(self) -> None:
        """A failed page is still a page that will not be retried; excluding it
        would leave the bar stuck short of 100%."""
        session = make_session(status=CrawlStatus.FETCHING, products_total=10, products_done=6, products_failed=4)
        assert session.progress == pytest.approx(1.0)

    def test_completed_is_always_one(self) -> None:
        session = make_session(status=CrawlStatus.COMPLETED, products_total=100, products_done=42)
        assert session.progress == pytest.approx(1.0)

    def test_never_exceeds_one(self) -> None:
        session = make_session(status=CrawlStatus.FETCHING, products_total=5, products_done=99)
        assert session.progress == pytest.approx(1.0)


class TestCrawlProductDerivedFields:
    def test_primary_rank_is_the_narrowest(self) -> None:
        product = CrawlProduct(
            best_seller_ranks=[{"rank": 203, "category": "Clothing"}, {"rank": 2, "category": "Sweatshirts"}]
        )
        assert product.primary_rank == {"rank": 2, "category": "Sweatshirts"}

    def test_primary_rank_without_ranks(self) -> None:
        assert CrawlProduct(best_seller_ranks=[]).primary_rank is None

    def test_revenue(self) -> None:
        assert CrawlProduct(price=10.0, bought_past_month=500).estimated_monthly_revenue == pytest.approx(5000.0)

    def test_revenue_is_none_when_volume_unknown(self) -> None:
        """Amazon hides the volume widget for low-volume listings; a fake zero
        would drag down every aggregate."""
        assert CrawlProduct(price=10.0, bought_past_month=None).estimated_monthly_revenue is None

    def test_revenue_is_none_without_price(self) -> None:
        assert CrawlProduct(price=None, bought_past_month=500).estimated_monthly_revenue is None


class TestValidateRequest:
    def test_trims_and_collapses_keywords(self) -> None:
        keywords, _ = validate_request(["  coffee   mug ", "tumbler"], "us", None, 10)
        assert keywords == ["coffee mug", "tumbler"]

    def test_drops_blank_keywords(self) -> None:
        keywords, _ = validate_request(["mug", "   ", ""], "us", None, 10)
        assert keywords == ["mug"]

    def test_rejects_all_blank(self) -> None:
        with pytest.raises(ValueError, match="At least one keyword"):
            validate_request(["  "], "us", None, 10)

    def test_rejects_too_many_keywords(self) -> None:
        with pytest.raises(ValueError, match=f"At most {MAX_KEYWORDS}"):
            validate_request([f"k{i}" for i in range(MAX_KEYWORDS + 1)], "us", None, 10)

    @pytest.mark.parametrize("value", [0, -1, MAX_PRODUCTS + 1])
    def test_rejects_bad_max_products(self, value: int) -> None:
        with pytest.raises(ValueError, match="max_products must be"):
            validate_request(["mug"], "us", None, value)

    def test_defaults_to_the_storefront_location(self) -> None:
        _, location = validate_request(["mug"], "us", None, 10)
        assert location == "10001"

    def test_resolves_a_preset(self) -> None:
        _, location = validate_request(["mug"], "us", "los-angeles", 10)
        assert location == "90001"

    def test_rejects_a_postcode_from_another_country(self) -> None:
        """Amazon accepts it, ignores it, and then every price is null. Failing
        at request time turns a twenty-minute mystery into a 400."""
        with pytest.raises(ValueError, match="not a valid US postcode"):
            validate_request(["mug"], "us", "SW1A1AA", 10)

    def test_location_none_is_allowed(self) -> None:
        _, location = validate_request(["mug"], "us", "none", 10)
        assert location is None


class TestQualityReport:
    def test_empty_batch(self) -> None:
        report, warnings = quality_report([])
        assert report["products"] == 0
        assert warnings == ["No products were parsed"]

    def test_healthy_batch_has_no_warnings(self) -> None:
        products = [
            make_product(
                asin=f"B00000000{i}",
                brand="X",
                price=10.0,
                currency="USD",
                rating=4.5,
                review_count=100,
                bought_past_month=500,
                image_url="http://img",
                best_seller_ranks=[BestSellerRank(rank=1, category="Mugs")],
                parse_confidence=1.0,
            )
            for i in range(3)
        ]
        report, warnings = quality_report(products, region="us")
        assert report["coverage"]["price"] == pytest.approx(1.0)
        assert report["currencies"] == {"USD": 3}
        assert warnings == []

    def test_flags_wrong_currency(self) -> None:
        products = [make_product(price=289993.0, currency="VND", bought_past_month=1)]
        _, warnings = quality_report(products, region="us")
        assert any("not all in USD" in w for w in warnings)

    def test_flags_mixed_currencies(self) -> None:
        products = [
            make_product(asin="B000000001", price=10.0, currency="USD"),
            make_product(asin="B000000002", price=10.0, currency="VND"),
        ]
        _, warnings = quality_report(products, region="us")
        assert any("Mixed currencies" in w for w in warnings)

    def test_flags_unshippable_and_says_it_is_not_zero(self) -> None:
        products = [make_product(unshippable=True)]
        report, warnings = quality_report(products, region="us", location="99546")
        assert report["unshippable"] == 1
        assert any("unknown, not zero" in w and "99546" in w for w in warnings)

    def test_missing_prices_without_shipping_blocks_means_drift(self) -> None:
        products = [make_product(asin=f"B00000000{i}", price=None) for i in range(3)]
        _, warnings = quality_report(products, region="us")
        assert any("selector may have drifted" in w for w in warnings)

    def test_shipping_blocks_suppress_the_drift_warning(self) -> None:
        """Both symptoms are 'no price', but the fixes are opposite; reporting
        drift here would send someone to edit selectors for nothing."""
        products = [make_product(asin=f"B00000000{i}", price=None, unshippable=True) for i in range(3)]
        _, warnings = quality_report(products, region="us")
        assert not any("drifted" in w for w in warnings)

    def test_uses_the_regions_currency(self) -> None:
        products = [make_product(price=10.0, currency="GBP", bought_past_month=1)]
        report, warnings = quality_report(products, region="uk")
        assert report["expected_currency"] == "GBP"
        assert not any("not all in" in w for w in warnings)
