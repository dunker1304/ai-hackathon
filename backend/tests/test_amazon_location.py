"""Delivery-location resolution and validation.

Amazon hides the buybox — and therefore the price — when the configured
delivery address cannot receive the item, so getting this wrong produces a
crawl full of `price: null` that looks like a parser bug.
"""

import pytest

from app.crawler.core.exceptions import CrawlerError
from app.crawler.marketplaces.amazon.location import (
    DEFAULT_LOCATIONS,
    DeliveryLocation,
    available_presets,
    normalize_zip,
    resolve_location,
    validate_zip,
)


class TestNormalizeZip:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("SW1A 1AA", "SW1A1AA"), ("m5h 2n2", "M5H2N2"), ("10001", "10001"), ("10001-1234", "100011234")],
    )
    def test_strips_spaces_and_uppercases(self, raw: str, expected: str) -> None:
        assert normalize_zip(raw) == expected


class TestValidateZip:
    @pytest.mark.parametrize(
        ("region", "zip_code"),
        [
            ("us", "10001"),
            ("us", "90210-1234"),
            ("uk", "SW1A 1AA"),
            ("uk", "M1 1AE"),
            ("de", "10115"),
            ("ca", "M5H 2N2"),
            ("au", "2000"),
        ],
    )
    def test_accepts_valid(self, region: str, zip_code: str) -> None:
        assert validate_zip(region, zip_code)

    @pytest.mark.parametrize(
        ("region", "zip_code"),
        [
            ("us", "SW1A1AA"),  # UK postcode on the US storefront
            ("us", "999"),
            ("us", "ABCDE"),
            ("uk", "10001"),  # US ZIP on the UK storefront
            ("au", "10001"),  # 5 digits, AU wants 4
        ],
    )
    def test_rejects_wrong_country(self, region: str, zip_code: str) -> None:
        """The endpoint accepts these and reports success, but never applies
        them -- every price then comes back empty."""
        with pytest.raises(CrawlerError, match="not a valid"):
            validate_zip(region, zip_code)

    def test_error_names_the_expected_format(self) -> None:
        with pytest.raises(CrawlerError, match="SW1A 1AA"):
            validate_zip("uk", "10001")

    def test_returns_normalized(self) -> None:
        assert validate_zip("uk", "sw1a 1aa") == "SW1A1AA"

    def test_unknown_region(self) -> None:
        with pytest.raises(CrawlerError, match="No postcode format"):
            validate_zip("mars", "10001")


class TestResolveLocation:
    def test_none_gives_the_storefront_default(self) -> None:
        assert resolve_location("us", None) == DEFAULT_LOCATIONS["us"]
        assert resolve_location("de", None).zip_code == "10115"

    @pytest.mark.parametrize("disable", ["none", "off", "skip", "NONE"])
    def test_disabling(self, disable: str) -> None:
        assert resolve_location("us", disable) is None

    def test_preset_by_name(self) -> None:
        location = resolve_location("us", "los-angeles")
        assert location.zip_code == "90001"
        assert location.label == "Los Angeles, CA"

    def test_preset_is_case_insensitive(self) -> None:
        assert resolve_location("us", "Los-Angeles").zip_code == "90001"

    def test_raw_postcode(self) -> None:
        location = resolve_location("us", "98101")
        assert location.zip_code == "98101"
        assert location.country == "US"

    def test_postcode_is_normalized(self) -> None:
        assert resolve_location("uk", "sw1a 1aa").zip_code == "SW1A1AA"

    def test_preset_from_another_storefront_is_rejected(self) -> None:
        with pytest.raises(CrawlerError, match="not a valid UK postcode"):
            resolve_location("uk", "los-angeles")

    def test_wrong_country_postcode_is_rejected(self) -> None:
        with pytest.raises(CrawlerError, match="not a valid US postcode"):
            resolve_location("us", "SW1A 1AA")

    def test_unknown_region(self) -> None:
        with pytest.raises(CrawlerError, match="Unknown Amazon region"):
            resolve_location("mars", None)

    def test_every_default_validates_against_its_own_region(self) -> None:
        """Guards against a typo in the preset table shipping a postcode that
        Amazon would silently ignore."""
        for region, location in DEFAULT_LOCATIONS.items():
            assert validate_zip(region, location.zip_code) == location.zip_code

    def test_every_named_preset_resolves(self) -> None:
        for region in DEFAULT_LOCATIONS:
            for name in available_presets(region):
                assert resolve_location(region, name) is not None


class TestGlowVerification:
    """`matches` is what proves Amazon actually applied the location; the
    endpoint reports success either way."""

    def test_matches_city_name(self) -> None:
        assert DEFAULT_LOCATIONS["us"].matches("New York 10001\u200c")

    def test_matches_postcode_only(self) -> None:
        assert DEFAULT_LOCATIONS["de"].matches("10115 Berlin")

    def test_is_case_insensitive(self) -> None:
        assert DEFAULT_LOCATIONS["uk"].matches("LONDON SW1A 1AA")

    def test_rejects_a_different_location(self) -> None:
        """The failure that motivated all of this: the widget still reads
        "Vietnam" because the postcode never applied."""
        assert not DEFAULT_LOCATIONS["us"].matches("Vietnam")

    def test_rejects_missing_widget(self) -> None:
        assert not DEFAULT_LOCATIONS["us"].matches(None)
        assert not DEFAULT_LOCATIONS["us"].matches("")

    def test_user_postcode_verifies_on_digits(self) -> None:
        location = resolve_location("us", "98101")
        assert location.matches("Seattle 98101")
        assert not location.matches("New York 10001")


class TestDeliveryLocationDataclass:
    def test_is_hashable_and_frozen(self) -> None:
        location = DeliveryLocation("10001", "NY", "US", ("10001",))
        assert {location}
        with pytest.raises(AttributeError):
            location.zip_code = "90210"  # type: ignore[misc]
