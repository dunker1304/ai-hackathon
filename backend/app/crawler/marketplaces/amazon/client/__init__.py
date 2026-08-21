"""Amazon HTTP clients, one per endpoint family.

`AmazonClient` combines them so a crawl run shares a single browser pool,
a single rate limiter and a single set of locale cookies across both the
search and the detail phase -- opening a second pool for the detail pass would
double the fingerprints Amazon sees for one logical session.
"""

from app.crawler.marketplaces.amazon.client.base import AmazonBaseClient
from app.crawler.marketplaces.amazon.client.product import AmazonProductClient
from app.crawler.marketplaces.amazon.client.search import AmazonSearchClient


class AmazonClient(AmazonSearchClient, AmazonProductClient):
    """Full-surface Amazon client: search + detail.

    async with AmazonClient(region="us", pool=pool) as client:
        crawler = AmazonCrawler(client)
        links = await crawler.collect_product_links("coffee mug")
        batch = await crawler.fetch_product_details(links.links)
    """


__all__ = [
    "AmazonBaseClient",
    "AmazonClient",
    "AmazonProductClient",
    "AmazonSearchClient",
]
