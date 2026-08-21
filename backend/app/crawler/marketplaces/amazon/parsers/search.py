"""Parse a search SERP into product links + the next-page pointer.

Pure functions over HTML strings: no network, no browser. That keeps the whole
extraction path testable against a saved fixture.
"""

from __future__ import annotations

import logging
import re

from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from app.crawler.core.exceptions import ParseError
from app.crawler.marketplaces.amazon import constants as const
from app.crawler.marketplaces.amazon.schemas import ProductLink, SearchPage
from app.crawler.marketplaces.amazon.url import extract_asin, product_url

logger = logging.getLogger(__name__)

TOTAL_RESULTS_RE = re.compile(r'"totalResultCount":(\d+)')


def _first_matching(tree: HTMLParser, selectors: tuple[str, ...]) -> tuple[list[Node], str | None]:
    """Amazon ships several grid layouts; take the first selector that hits."""
    for selector in selectors:
        nodes = tree.css(selector)
        if nodes:
            return nodes, selector
    return [], None


def find_result_cards(tree: HTMLParser) -> list[Node]:
    nodes, selector = _first_matching(tree, const.RESULT_CARD_SELECTORS)
    if nodes:
        logger.debug("Matched %d cards via %r", len(nodes), selector)
    return nodes


def card_asin(card: Node) -> str | None:
    """Resolve the ASIN of a result card.

    A card contains ~5 `a.a-link-normal` elements (title, image, reviews, a
    `javascript:void(0)` badge, sometimes a brand ad), so link order is not
    reliable. Collect ASINs from every href and accept the unique one; the
    `data-asin` attribute is the fallback.
    """
    found: set[str] = set()
    for link in card.css(const.PRODUCT_LINK_SELECTOR):
        href = link.attributes.get("href") or ""
        asin = extract_asin(href)
        if asin:
            found.add(asin)

    if len(found) == 1:
        return found.pop()

    attr = card.attributes.get("data-asin")
    if attr:
        return attr
    if found:
        # Ambiguous card (variation grid); the DOM order puts the main product first.
        logger.debug("Card exposed %d ASINs, falling back to data-asin/first", len(found))
        return min(found)
    return None


def card_title(card: Node) -> str | None:
    node = card.css_first(const.SELECTORS["title"])
    if node is None:
        return None
    text = node.text(strip=True)
    return text or None


def card_is_sponsored(card: Node) -> bool:
    """Detect ad placements.

    Substring-matching "Sponsored" over the card HTML is wrong: every card
    embeds a JSON blob containing `isSponsored":""` and
    `searchProductType":"ORGANIC"`, so a naive check flags 27/48 organic cards
    as ads. Read the structured signals instead.
    """
    if card.css_first(const.SELECTORS["sponsored"]) is not None:
        return True

    html = card.html or ""
    product_type = const.SEARCH_PRODUCT_TYPE_RE.search(html)
    if product_type is not None:
        return product_type.group(1) != "ORGANIC"

    is_sponsored = const.IS_SPONSORED_RE.search(html)
    if is_sponsored is not None:
        return bool(is_sponsored.group(1))

    # Last resort: the visible label, which lives in its own element.
    label = card.css_first(const.SELECTORS["sponsored_label"])
    return label is not None and label.text(strip=True).lower().startswith("sponsored")


def find_next_page_url(tree: HTMLParser, *, base_url: str) -> str | None:
    """Return the absolute next-page URL, or None when pagination is exhausted.

    When there is no next page Amazon renders a `<span class="...
    s-pagination-disabled">` instead of an `<a href>`, so the absence of an
    href is the stop condition.
    """
    for selector in const.NEXT_PAGE_SELECTORS:
        node = tree.css_first(selector)
        if node is None:
            continue
        if node.attributes.get("aria-disabled") == "true":
            return None
        href = node.attributes.get("href")
        if href:
            return urljoin(base_url, href)
    return None


def parse_total_results(html: str) -> int | None:
    match = TOTAL_RESULTS_RE.search(html)
    return int(match.group(1)) if match else None


def parse_search_page(
    html: str,
    *,
    url: str,
    page: int = 1,
    region: str = const.DEFAULT_REGION,
    keyword: str | None = None,
    include_sponsored: bool = True,
    start_position: int = 1,
) -> SearchPage:
    """Extract every product link on one SERP.

    Raises `ParseError` when no cards match *and* the page is not an explicit
    "no results" page -- that combination means the selectors have drifted, and
    silently returning an empty list would hide it.
    """
    tree = HTMLParser(html)
    cards = find_result_cards(tree)

    if not cards:
        if tree.css_first(const.SELECTORS["no_results"]) is not None:
            logger.info("No results for %r", keyword or url)
            return SearchPage(url=url, page=page, links=[], next_page_url=None)
        raise ParseError(
            f"No result cards matched any of {const.RESULT_CARD_SELECTORS}. "
            f"Amazon likely changed its layout, or the page is an interstitial.",
            url=url,
        )

    links: list[ProductLink] = []
    seen: set[str] = set()
    position = start_position

    for card in cards:
        asin = card_asin(card)
        if asin is None or asin in seen:
            continue
        sponsored = card_is_sponsored(card)
        if sponsored and not include_sponsored:
            continue
        seen.add(asin)
        links.append(
            ProductLink(
                asin=asin,
                url=product_url(asin, region),
                title=card_title(card),
                position=position,
                page=page,
                sponsored=sponsored,
                keyword=keyword,
            )
        )
        position += 1

    return SearchPage(
        url=url,
        page=page,
        links=links,
        next_page_url=find_next_page_url(tree, base_url=const.BASE_URLS[region]),
        total_result_count=parse_total_results(html),
    )
