"""Base URLs, selectors, and query-param vocabulary for Amazon.

Everything here was verified against a real SERP; see PLAN.md for the numbers.
Amazon rotates layouts, so selectors are lists tried in order rather than single
strings.
"""

import re

from app.crawler.core.types import TimeWindow

DEFAULT_REGION = "us"

BASE_URLS: dict[str, str] = {
    "us": "https://www.amazon.com",
    "uk": "https://www.amazon.co.uk",
    "de": "https://www.amazon.de",
    "ca": "https://www.amazon.ca",
    "au": "https://www.amazon.com.au",
}

SEARCH_PATH = "/s"
PRODUCT_PATH = "/dp/{asin}"
BESTSELLER_PATH = "/gp/bestsellers/{node}"

#: Pins the US storefront + USD pricing regardless of the exit IP.
#: Without these, a VN IP renders prices as "VND 913,570".
US_LOCALE_COOKIES: list[dict[str, str]] = [
    {"name": "i18n-prefs", "value": "USD", "domain": ".amazon.com", "path": "/"},
    {"name": "lc-main", "value": "en_US", "domain": ".amazon.com", "path": "/"},
]

#: Currency cookies are not enough. With a delivery address outside the
#: storefront's country Amazon hides the buybox entirely ("This item cannot be
#: shipped to your selected delivery location"), so `#corePrice_feature_div`
#: does not exist and price coverage collapses. The "glow" widget stores the
#: address behind this endpoint; presets live in `location.py`.
#:
#: The modern JSON endpoint `/portal-migration/hz/glow/address-change` answers
#: HTTP 200 but does not apply -- only this legacy path works.
GLOW_ENDPOINT = "/gp/delivery/ajax/address-change.html"
GLOW_INGRESS_SELECTOR = "#glow-ingress-line2"

#: The currency each storefront should quote once its delivery location is set.
#: A mismatch means the location did not stick.
REGION_CURRENCIES: dict[str, str] = {
    "us": "USD",
    "uk": "GBP",
    "de": "EUR",
    "ca": "CAD",
    "au": "AUD",
}

#: Marks a page whose buybox was suppressed by the delivery location.
#: Whitespace-tolerant: the sentence wraps across lines in the served HTML.
UNSHIPPABLE_RE = re.compile(r"cannot\s+be\s+shipped\s+to\s+your\s+selected\s+delivery\s+location", re.IGNORECASE)

# --- selectors ------------------------------------------------------------
# Tried in order; the first selector that yields nodes wins.

RESULT_CARD_SELECTORS: tuple[str, ...] = (
    '[data-component-type="s-search-result"]',
    ".a-section.a-spacing-base.desktop-grid-content-view",
    ".s-result-item[data-asin]",
)

#: Any product link inside a card. The ASIN is extracted from the href rather
#: than trusting link order -- a card holds ~5 of these, including
#: `javascript:void(0)` badges and brand-ad links.
PRODUCT_LINK_SELECTOR = "a.a-link-normal"

NEXT_PAGE_SELECTORS: tuple[str, ...] = (
    "a.s-pagination-next",
    'a[aria-label="Go to next page"]',
)

SELECTORS: dict[str, str] = {
    "title": "h2",
    "price": ".a-price .a-offscreen",
    "price_whole": ".a-price-whole",
    "rating": ".a-icon-star-small .a-icon-alt",
    "review_count": '[data-cy="reviews-block"] .s-underline-text',
    "sponsored": '[data-component-type="sp-sponsored-result"]',
    "sponsored_label": ".puis-sponsored-label-text, .s-sponsored-label-text",
    "image": "img.s-image",
    "bought_past_month": ".a-size-base.a-color-secondary",
    "no_results": '[data-cy="no-results-header"], .s-no-outline .a-size-medium',
}

#: Structured ad signals embedded in each card's JSON attributes.
#: Plain substring search for "Sponsored" produces false positives: organic
#: cards carry `isSponsored":""` and `searchProductType":"ORGANIC"`.
#: Values are HTML-escaped inside the attribute, hence the `&quot;`.
SEARCH_PRODUCT_TYPE_RE = re.compile(r"searchProductType(?:&quot;|\"):(?:&quot;|\")([A-Z_]+)")
IS_SPONSORED_RE = re.compile(r"isSponsored(?:&quot;|\"):(?:&quot;|\")([^&\"]*)")

# --- product detail page --------------------------------------------------
# Verified against a real /dp page; see PLAN.md for the pitfalls behind each
# choice. Every entry is a comma-separated list so selectolax tries all of them.

DETAIL_SELECTORS: dict[str, str] = {
    "title": "#productTitle",
    "byline": "#bylineInfo",
    # Scope the price: the page holds 33 `.a-price .a-offscreen` nodes
    # (variations, "similar items", ads). Only the buybox one is this product's.
    "price": "#corePrice_feature_div .a-offscreen, #corePriceDisplay_desktop_feature_div .a-offscreen",
    "list_price": "#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen",
    "rating": "#acrPopover .a-icon-alt, #averageCustomerReviews .a-icon-alt",
    "review_count": "#acrCustomerReviewText",
    "bought_past_month": "#social-proofing-faceout-title-tk_bought",
    "availability": "#availability span",
    "bullets": "#productFactsDesktopExpander li, #feature-bullets ul li span.a-list-item",
    "image": "#landingImage",
    "asin_input": "input#ASIN, input[name='ASIN']",
    "canonical": "link[rel='canonical']",
    "categories": "#wayfinding-breadcrumbs_feature_div a",
}

#: Attribute tables. Must be scoped to `table.prodDetTable`: a bare `tr th+td`
#: query also matches the apparel size chart (XS=30-32, S=34-36, ...).
DETAIL_TABLE_ROW_SELECTOR = "table.prodDetTable tr"
DETAIL_TABLE_KEY_SELECTOR = "th.prodDetSectionEntry"

#: Fallback "key ‏ : ‎ value" bullet list used on non-apparel listings.
DETAIL_BULLET_SELECTOR = "#detailBulletsWrapper_feature_div li"

#: Amazon separates label and value with these invisible bidi marks.
BIDI_MARKS = "\u200e\u200f\u202a\u202b\u202c\u2066\u2067\u2068\u2069"

#: Attribute-table keys worth promoting to first-class fields.
ATTR_KEY_ASIN = "ASIN"
ATTR_KEY_BSR = "Best Sellers Rank"
ATTR_KEY_BRAND = "Brand Name"
ATTR_KEY_MANUFACTURER = "Manufacturer"
ATTR_KEY_DATE_FIRST_AVAILABLE = "Date First Available"

#: "#203 in Clothing, Shoes & Jewelry" / "#2 inMen's Sweatshirts".
#: The space after "in" is missing when the category is a link, so `\s*`
#: rather than `\s+`; without it the narrow (most useful) rank is dropped.
BSR_RE = re.compile(r"#([\d,]+)\s+in\s*([^(#]+?)(?:\s*\(|\s*#|$)")

#: BSR entries live in their own <li>; parsing them individually keeps the
#: "(See Top 100 in ...)" suffix from swallowing the next entry.
BSR_ROW_SELECTOR = "li"

#: "500+ bought in past month" -- Amazon's only first-party 30-day volume signal.
#: Rendered without a space after the number ("500+ boughtin past month").
BOUGHT_RE = re.compile(r"([\d,.]+)\s*([KkMm])?\+?\s*bought", re.IGNORECASE)

#: "4.6 out of 5 stars"
RATING_RE = re.compile(r"([\d.]+)\s+out of\s+5")

#: "(141,921)" or "141,921 ratings"
REVIEW_COUNT_RE = re.compile(r"([\d,]+)")

#: "$11.11" / "VND289,993" / "£9.99"
PRICE_RE = re.compile(r"(?P<symbol>[A-Z]{3}|[$£€¥₫])\s?(?P<amount>[\d,.]+)")

CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
    "₫": "VND",
}

#: Variation ("twister") map: child ASIN -> [dimension values].
TWISTER_RE = re.compile(r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})\s*,\s*"', re.DOTALL)

# --- query params ---------------------------------------------------------

PARAM_KEYWORD = "k"
PARAM_PAGE = "page"
PARAM_SORT = "s"
PARAM_DEPARTMENT = "i"
PARAM_REFINEMENT = "rh"

SORT_PARAMS: dict[str, str] = {
    "relevance": "relevanceblender",
    "best_seller": "exact-aware-popularity-rank",
    "newest": "date-desc-rank",
    "price_asc": "price-asc-rank",
    "price_desc": "price-desc-rank",
    "rating": "review-rank",
}

#: `rh` refinement prefixes.
RH_CATEGORY = "n"
RH_PRICE = "p_36"
RH_RATING = "p_72"
RH_PRIME = "p_85"
RH_DATE_FIRST_AVAILABLE = "p_n_date_first_available_absolute"

#: 4 stars & up. rnid observed in the captured SERP.
RATING_RNIDS: dict[int, str] = {4: "2661618011"}

PRIME_RNID = "2470955011"

#: Amazon's "New Arrivals" rail maps a TimeWindow onto an rnid, but the rnid is
#: **department-specific** and was not present in the captured fixture.
#: Populate per department before enabling time filters; see PLAN.md.
#: Shape: {department: {TimeWindow: rnid}}
DATE_FIRST_AVAILABLE_RNIDS: dict[str, dict[TimeWindow, str]] = {}

# --- pagination limits ----------------------------------------------------

#: Results per SERP observed in the fixture (48 organic cards).
RESULTS_PER_PAGE = 48

#: Amazon stops serving organic results past this page regardless of the
#: advertised total result count.
MAX_SEARCH_PAGES = 7
