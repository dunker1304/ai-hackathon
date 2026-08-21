"""Amazon marketplace package.

    from app.crawler.marketplaces.amazon import AmazonCrawler, AmazonSearchClient

    async with AmazonSearchClient(pool=pool) as client:
        result = await AmazonCrawler(client).collect_product_links("coffee mug")

See PLAN.md for verified selectors, pagination behaviour and known pitfalls.
"""

from app.crawler.marketplaces.amazon.client import (
    AmazonBaseClient,
    AmazonClient,
    AmazonProductClient,
    AmazonSearchClient,
)
from app.crawler.marketplaces.amazon.crawler import AmazonCrawler
from app.crawler.marketplaces.amazon.schemas import (
    AmazonProduct,
    BestSellerRank,
    LinkCollection,
    ProductBatch,
    ProductLink,
    SearchPage,
)
from app.crawler.marketplaces.amazon.url import SearchQuery, build_search_url, extract_asin, product_url

__all__ = [
    "AmazonBaseClient",
    "AmazonClient",
    "AmazonCrawler",
    "AmazonProduct",
    "AmazonProductClient",
    "AmazonSearchClient",
    "BestSellerRank",
    "LinkCollection",
    "ProductBatch",
    "ProductLink",
    "SearchPage",
    "SearchQuery",
    "build_search_url",
    "extract_asin",
    "product_url",
]
