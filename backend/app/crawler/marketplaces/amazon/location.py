"""Delivery location: the thing that decides whether prices exist at all.

Amazon renders the buybox only when the configured delivery address can
actually receive the item. From a Vietnamese IP most US listings come back as
"This item cannot be shipped to your selected delivery location" with **no
price element in the DOM at all** -- the selector is not broken, the price was
never rendered.

So the crawl must (a) set a delivery location that belongs to the target
storefront, and (b) verify Amazon accepted it before trusting a single price.
"""

from __future__ import annotations

import re

from dataclasses import dataclass

from app.crawler.core.exceptions import CrawlerError


@dataclass(frozen=True, slots=True)
class DeliveryLocation:
    """A postcode plus what the glow widget should read once it is applied."""

    zip_code: str
    label: str
    country: str
    #: Substrings that must appear in `#glow-ingress-line2` after the change.
    #: Amazon formats the widget differently per storefront ("New York 10001",
    #: "London SW1A 1AA", "10115 Berlin"), so matching is a contains-check
    #: against these rather than an equality test.
    expect: tuple[str, ...]

    def matches(self, glow_text: str | None) -> bool:
        if not glow_text:
            return False
        haystack = glow_text.casefold()
        return any(token.casefold() in haystack for token in self.expect)


#: Curated defaults, one per storefront: a large metro that virtually every
#: seller ships to. A rural postcode would silently suppress the buybox on a
#: subset of listings and look like a parser bug.
DEFAULT_LOCATIONS: dict[str, DeliveryLocation] = {
    "us": DeliveryLocation("10001", "New York, NY", "US", ("10001", "new york")),
    "uk": DeliveryLocation("SW1A1AA", "London", "GB", ("sw1a", "london")),
    "de": DeliveryLocation("10115", "Berlin", "DE", ("10115", "berlin")),
    "ca": DeliveryLocation("M5H2N2", "Toronto, ON", "CA", ("m5h", "toronto")),
    "au": DeliveryLocation("2000", "Sydney, NSW", "AU", ("2000", "sydney")),
}

#: Extra presets so `--location` can take a city name instead of a postcode.
NAMED_LOCATIONS: dict[str, dict[str, DeliveryLocation]] = {
    "us": {
        "new-york": DEFAULT_LOCATIONS["us"],
        "los-angeles": DeliveryLocation("90001", "Los Angeles, CA", "US", ("90001", "los angeles")),
        "chicago": DeliveryLocation("60601", "Chicago, IL", "US", ("60601", "chicago")),
        "houston": DeliveryLocation("77001", "Houston, TX", "US", ("77001", "houston")),
        "seattle": DeliveryLocation("98101", "Seattle, WA", "US", ("98101", "seattle")),
    },
    "uk": {
        "london": DEFAULT_LOCATIONS["uk"],
        "manchester": DeliveryLocation("M11AE", "Manchester", "GB", ("m1 1ae", "m11ae", "manchester")),
    },
    "de": {
        "berlin": DEFAULT_LOCATIONS["de"],
        "munich": DeliveryLocation("80331", "Munich", "DE", ("80331", "münchen", "munich")),
    },
    "ca": {"toronto": DEFAULT_LOCATIONS["ca"]},
    "au": {"sydney": DEFAULT_LOCATIONS["au"]},
}

#: Postcode shapes per storefront. Validating up front turns a silent
#: "prices are all null" run into an immediate, actionable error.
ZIP_PATTERNS: dict[str, re.Pattern[str]] = {
    "us": re.compile(r"^\d{5}(-\d{4})?$"),
    "uk": re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$", re.IGNORECASE),
    "de": re.compile(r"^\d{5}$"),
    "ca": re.compile(r"^[A-Z]\d[A-Z]\s?\d[A-Z]\d$", re.IGNORECASE),
    "au": re.compile(r"^\d{4}$"),
}

ZIP_EXAMPLES: dict[str, str] = {
    "us": "10001",
    "uk": "SW1A 1AA",
    "de": "10115",
    "ca": "M5H 2N2",
    "au": "2000",
}


def normalize_zip(zip_code: str) -> str:
    """Amazon's glow endpoint wants the postcode without internal spaces."""
    return zip_code.replace(" ", "").replace("-", "").upper()


def validate_zip(region: str, zip_code: str) -> str:
    """Check the postcode belongs to the storefront, and return it normalized.

    A UK postcode sent to amazon.com is accepted by the endpoint but never
    applied, so every price silently disappears. Catching the mismatch here is
    the difference between a clear error and an hour of debugging selectors.
    """
    pattern = ZIP_PATTERNS.get(region)
    if pattern is None:
        raise CrawlerError(f"No postcode format known for region {region!r}")

    candidate = zip_code.strip()
    if not pattern.match(candidate) and not pattern.match(normalize_zip(candidate)):
        raise CrawlerError(
            f"{zip_code!r} is not a valid {region.upper()} postcode "
            f"(expected something like {ZIP_EXAMPLES[region]!r}). "
            f"A postcode from the wrong country is accepted by Amazon but never "
            f"applied, and every price comes back empty."
        )
    return normalize_zip(candidate)


def resolve_location(region: str, value: str | None) -> DeliveryLocation | None:
    """Turn CLI input into a `DeliveryLocation`.

    `value` may be:
      * `None`      -> the storefront default
      * `"none"`    -> disable location setting entirely (accepts whatever
                       Amazon infers from the exit IP)
      * a preset    -> `"los-angeles"`, `"berlin"`, ...
      * a postcode  -> `"90210"`, `"SW1A 1AA"`
    """
    if region not in DEFAULT_LOCATIONS:
        raise CrawlerError(f"Unknown Amazon region {region!r}; known: {sorted(DEFAULT_LOCATIONS)}")

    if value is None:
        return DEFAULT_LOCATIONS[region]

    key = value.strip().lower()
    if key in {"none", "off", "skip"}:
        return None

    preset = NAMED_LOCATIONS.get(region, {}).get(key)
    if preset is not None:
        return preset

    if key in NAMED_LOCATIONS and key != region:
        raise CrawlerError(
            f"{value!r} is a preset for another storefront, not {region!r}. "
            f"Presets for {region!r}: {sorted(NAMED_LOCATIONS.get(region, {}))}"
        )

    zip_code = validate_zip(region, value)
    return DeliveryLocation(
        zip_code=zip_code,
        label=zip_code,
        country=region.upper(),
        # A user-supplied postcode: the widget shows the postcode itself, but
        # the city name is unknown, so only the digits can be verified.
        expect=(zip_code, zip_code[:5]),
    )


def available_presets(region: str) -> list[str]:
    return sorted(NAMED_LOCATIONS.get(region, {}))
