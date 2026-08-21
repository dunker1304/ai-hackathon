"""Crawl orchestration: sessions, persistence and background execution.

`app.crawler` is the scraping engine (browsers, parsers, marketplaces).
`app.crawl` is what the API and the workers talk to: it owns the
`CrawlSession` lifecycle and writes results into Postgres.
"""

from app.crawl.repository import CrawlRepository
from app.crawl.service import create_session, start_session

__all__ = ["CrawlRepository", "create_session", "start_session"]
