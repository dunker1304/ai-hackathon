"""Parse a /dp product page into an `AmazonProduct`.

Pure functions over HTML, same contract as the search parser: no network, no
browser, fully testable against a saved page.
"""

from __future__ import annotations

import json
import logging
import re

from datetime import UTC, datetime

from selectolax.parser import HTMLParser, Node

from app.crawler.core.exceptions import ParseError
from app.crawler.marketplaces.amazon import constants as const
from app.crawler.marketplaces.amazon.schemas import AmazonProduct, BestSellerRank
from app.crawler.marketplaces.amazon.url import extract_asin, product_url

logger = logging.getLogger(__name__)

#: Fields that make a record useful downstream; drives `parse_confidence`.
CORE_FIELDS = ("title", "price", "rating", "review_count", "brand")

MULTIPLIERS = {"k": 1_000, "m": 1_000_000}


def clean(text: str | None) -> str | None:
    """Collapse whitespace and strip Amazon's invisible bidi marks."""
    if text is None:
        return None
    stripped = text.translate({ord(c): None for c in const.BIDI_MARKS})
    collapsed = " ".join(stripped.split())
    return collapsed or None


def _text(tree: HTMLParser | Node, selector: str) -> str | None:
    node = tree.css_first(selector)
    return clean(node.text(strip=True)) if node is not None else None


def parse_price(raw: str | None) -> tuple[float | None, str | None]:
    """`"$11.11"` -> `(11.11, "USD")`.

    Returns the currency too, because Amazon prices in the currency of the exit
    IP: from a VN address the same product renders as "VND289,993". Silently
    mixing the two would corrupt every revenue figure, so callers must check it.
    """
    if not raw:
        return None, None
    match = const.PRICE_RE.search(raw)
    if match is None:
        return None, None

    symbol = match.group("symbol")
    currency = const.CURRENCY_SYMBOLS.get(symbol, symbol if len(symbol) == 3 else None)

    amount = match.group("amount")
    # "289,993" (VND, no decimals) vs "1,234.56" (USD)
    if "." in amount or (amount.count(",") >= 1 and len(amount.rsplit(",", 1)[1]) == 3):
        amount = amount.replace(",", "")
    else:
        amount = amount.replace(",", ".")

    try:
        return float(amount), currency
    except ValueError:
        return None, currency


def parse_rating(raw: str | None) -> float | None:
    if not raw:
        return None
    match = const.RATING_RE.search(raw)
    return float(match.group(1)) if match else None


def parse_review_count(raw: str | None) -> int | None:
    if not raw:
        return None
    match = const.REVIEW_COUNT_RE.search(raw.replace("(", "").replace(")", ""))
    return int(match.group(1).replace(",", "")) if match else None


def parse_bought_past_month(raw: str | None) -> int | None:
    """`"500+ bought in past month"` -> `500`, `"2K+ bought..."` -> `2000`.

    This is a **floor**: Amazon buckets the number and omits the widget entirely
    for low-volume listings, so `None` means "unknown", never "zero".
    """
    if not raw:
        return None
    match = const.BOUGHT_RE.search(raw)
    if match is None:
        return None
    value = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").lower()
    return int(value * MULTIPLIERS.get(suffix, 1))


def parse_best_seller_ranks(raw: str | None) -> list[BestSellerRank]:
    """`"#203 in Clothing, Shoes & Jewelry (See Top 100...) #2 in Men's Sweatshirts"`
    -> two ranks. The narrower category is the meaningful demand signal."""
    if not raw:
        return []
    ranks: list[BestSellerRank] = []
    for match in const.BSR_RE.finditer(raw):
        category = clean(match.group(2))
        if not category or category.lower().startswith("see top"):
            continue
        ranks.append(BestSellerRank(rank=int(match.group(1).replace(",", "")), category=category))
    return ranks


def parse_bsr_from_tree(tree: HTMLParser) -> list[BestSellerRank]:
    """Read the BSR rows straight from the DOM.

    Preferred over regexing the flattened attribute string: each rank sits in
    its own `<li>`, so the "(See Top 100 in ...)" suffix of the broad rank
    cannot swallow the narrow one that follows it.
    """
    for row in tree.css(const.DETAIL_TABLE_ROW_SELECTOR):
        key_node = row.css_first(const.DETAIL_TABLE_KEY_SELECTOR)
        if key_node is None or const.ATTR_KEY_BSR not in key_node.text():
            continue
        ranks: list[BestSellerRank] = []
        for item in row.css(const.BSR_ROW_SELECTOR):
            ranks.extend(parse_best_seller_ranks(clean(item.text(strip=True))))
        if ranks:
            return ranks
    return []


def parse_attributes(tree: HTMLParser) -> dict[str, str]:
    """Collect the product-details table plus the bullet-style fallback.

    The table query is scoped to `table.prodDetTable`: a bare `tr th+td` sweep
    also picks up the apparel size chart (XS=30-32, S=34-36, ...), which is not
    product metadata.
    """
    attributes: dict[str, str] = {}

    for row in tree.css(const.DETAIL_TABLE_ROW_SELECTOR):
        key_node = row.css_first(const.DETAIL_TABLE_KEY_SELECTOR)
        value_node = row.css_first("td")
        if key_node is None or value_node is None:
            continue
        key = clean(key_node.text(strip=True))
        value = clean(value_node.text(strip=True))
        if key and value and key not in attributes:
            attributes[key] = value

    for item in tree.css(const.DETAIL_BULLET_SELECTOR):
        text = clean(item.text(strip=True))
        if not text or ":" not in text:
            continue
        key, _, value = text.partition(":")
        key, value = clean(key), clean(value)
        if key and value and key not in attributes:
            attributes[key] = value

    return attributes


def parse_bullets(tree: HTMLParser) -> list[str]:
    seen: list[str] = []
    for node in tree.css(const.DETAIL_SELECTORS["bullets"]):
        text = clean(node.text(strip=True))
        if text and text not in seen:
            seen.append(text)
    return seen


def parse_categories(tree: HTMLParser) -> list[str]:
    return [c for c in (clean(n.text(strip=True)) for n in tree.css(const.DETAIL_SELECTORS["categories"])) if c]


def parse_brand(tree: HTMLParser, attributes: dict[str, str]) -> str | None:
    """Prefer the structured attribute; fall back to the byline.

    The byline reads "Visit the Hanes Store" or "Brand: Hanes", so the prefix
    has to be stripped rather than stored verbatim.
    """
    for key in (const.ATTR_KEY_BRAND, const.ATTR_KEY_MANUFACTURER):
        if attributes.get(key):
            return attributes[key]

    byline = _text(tree, const.DETAIL_SELECTORS["byline"])
    if not byline:
        return None
    match = re.match(r"(?:Visit the\s+)?(.+?)(?:\s+Store)?$|^Brand:\s*(.+)$", byline)
    if match:
        return clean(match.group(1) or match.group(2))
    return byline


def parse_image_url(tree: HTMLParser) -> str | None:
    node = tree.css_first(const.DETAIL_SELECTORS["image"])
    if node is None:
        return None
    # `data-old-hires` is the full-resolution original; `src` is a thumbnail.
    hires = node.attributes.get("data-old-hires")
    if hires:
        return hires
    dynamic = node.attributes.get("data-a-dynamic-image")
    if dynamic:
        try:
            urls = json.loads(dynamic)
        except json.JSONDecodeError:
            urls = {}
        if urls:
            return max(urls, key=lambda u: urls[u][0] if urls[u] else 0)
    return node.attributes.get("src")


def parse_variations(html: str) -> int | None:
    """Count sibling ASINs in the twister widget (colours / sizes)."""
    match = const.TWISTER_RE.search(html)
    if match is None:
        return None
    try:
        return len(json.loads(match.group(1)))
    except json.JSONDecodeError:
        return None


def resolve_asin(tree: HTMLParser, *, url: str, attributes: dict[str, str]) -> str | None:
    """Trust the page over the URL.

    A /dp/<child> request can redirect to the parent listing, so the ASIN in
    the DOM is the one the rest of the page describes.
    """
    node = tree.css_first(const.DETAIL_SELECTORS["asin_input"])
    if node is not None:
        value = node.attributes.get("value")
        if value and re.fullmatch(r"[A-Z0-9]{10}", value):
            return value

    table_asin = attributes.get(const.ATTR_KEY_ASIN)
    if table_asin and re.fullmatch(r"[A-Z0-9]{10}", table_asin):
        return table_asin

    return extract_asin(url)


def resolve_parent_asin(tree: HTMLParser, *, asin: str) -> str | None:
    node = tree.css_first(const.DETAIL_SELECTORS["canonical"])
    if node is None:
        return None
    canonical = extract_asin(node.attributes.get("href") or "")
    return canonical if canonical and canonical != asin else None


def compute_confidence(product: AmazonProduct) -> float:
    filled = sum(getattr(product, field) is not None for field in CORE_FIELDS)
    return round(filled / len(CORE_FIELDS), 2)


def parse_product_page(
    html: str,
    *,
    url: str,
    keyword: str | None = None,
    position: int | None = None,
    region: str = const.DEFAULT_REGION,
    expected_currency: str | None = "USD",
) -> AmazonProduct:
    """Extract a product record from a /dp page.

    Raises `ParseError` when neither the ASIN nor the title can be found -- that
    means the page is an interstitial or the layout drifted, and returning an
    empty record would let junk into the database.

    `expected_currency` guards against IP-based currency switching; a mismatch
    is logged loudly because it silently breaks every revenue metric.
    """
    tree = HTMLParser(html)

    attributes = parse_attributes(tree)
    asin = resolve_asin(tree, url=url, attributes=attributes)
    title = _text(tree, const.DETAIL_SELECTORS["title"])

    if asin is None:
        raise ParseError("Could not resolve an ASIN; page is likely an interstitial", url=url)
    if title is None:
        raise ParseError(f"No #productTitle on {asin}; layout drifted or page was blocked", url=url)

    price, currency = parse_price(_text(tree, const.DETAIL_SELECTORS["price"]))
    list_price, _ = parse_price(_text(tree, const.DETAIL_SELECTORS["list_price"]))

    # Amazon removes the buybox entirely when the delivery address cannot
    # receive the item, so a missing price here is a location problem rather
    # than a parsing one. Recording which of the two it is keeps the pipeline
    # from reading "unavailable" as "free".
    unshippable = price is None and const.UNSHIPPABLE_RE.search(html) is not None
    if unshippable:
        logger.warning(
            "%s has no buybox: it does not ship to the configured delivery location. "
            "Set a delivery location inside the storefront's country (--location).",
            asin,
        )

    if expected_currency and currency and currency != expected_currency:
        logger.warning(
            "%s priced in %s, expected %s -- the exit IP is changing the storefront; "
            "revenue figures will be wrong until locale cookies are applied",
            asin,
            currency,
            expected_currency,
        )

    product = AmazonProduct(
        asin=asin,
        url=product_url(asin, region),
        title=title,
        brand=parse_brand(tree, attributes),
        price=price,
        list_price=list_price,
        currency=currency,
        rating=parse_rating(_text(tree, const.DETAIL_SELECTORS["rating"])),
        review_count=parse_review_count(_text(tree, const.DETAIL_SELECTORS["review_count"])),
        bought_past_month=parse_bought_past_month(_text(tree, const.DETAIL_SELECTORS["bought_past_month"])),
        availability=_text(tree, const.DETAIL_SELECTORS["availability"]),
        unshippable=unshippable,
        best_seller_ranks=(parse_bsr_from_tree(tree) or parse_best_seller_ranks(attributes.get(const.ATTR_KEY_BSR))),
        categories=parse_categories(tree),
        bullets=parse_bullets(tree),
        attributes=attributes,
        image_url=parse_image_url(tree),
        parent_asin=resolve_parent_asin(tree, asin=asin),
        variation_count=parse_variations(html),
        keyword=keyword,
        position=position,
        fetched_at=datetime.now(UTC),
    )
    product.parse_confidence = compute_confidence(product)
    return product
