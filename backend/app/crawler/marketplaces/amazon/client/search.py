"""Fetch /s search result pages (keyword, page, sort, filters)."""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from app.crawler.marketplaces.amazon import constants as const
from app.crawler.marketplaces.amazon.client.base import AmazonBaseClient
from app.crawler.marketplaces.amazon.url import SearchQuery, build_search_url

if TYPE_CHECKING:
    from app.crawler.core.client.base import FetchResponse

logger = logging.getLogger(__name__)


class AmazonSearchClient(AmazonBaseClient):
    """Fetches one SERP at a time. Pagination lives in the crawler."""

    async def fetch_search_page(self, query: SearchQuery, *, page: int = 1) -> FetchResponse:
        """Navigate to page `page` of `query` and return the rendered HTML.

        Waits for a result card rather than `networkidle`: Amazon keeps
        long-polling ad slots, so `networkidle` would burn the whole timeout on
        a page that is already usable.
        """
        url = build_search_url(query, page=page)
        logger.info("Amazon SERP page=%d keyword=%r", page, query.keyword)
        return await self.get(url, wait_for=list(const.RESULT_CARD_SELECTORS))
