"""Fetch /dp product detail pages (+ offers / reviews summary fragments)."""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from app.crawler.marketplaces.amazon import constants as const
from app.crawler.marketplaces.amazon.client.base import AmazonBaseClient
from app.crawler.marketplaces.amazon.url import product_url

if TYPE_CHECKING:
    from app.crawler.core.client.base import FetchResponse

logger = logging.getLogger(__name__)


class AmazonProductClient(AmazonBaseClient):
    """Fetches one /dp page at a time. Batching lives in the crawler."""

    async def fetch_product_page(self, asin: str) -> FetchResponse:
        """Navigate to a product page and return the rendered HTML.

        Waits for `#productTitle` rather than `networkidle`: the page keeps
        loading recommendation carousels long after the data we need is
        present, and `networkidle` would burn the whole timeout.
        """
        url = product_url(asin, self.region)
        logger.debug("Amazon detail asin=%s", asin)
        return await self.get(url, wait_for=const.DETAIL_SELECTORS["title"])
