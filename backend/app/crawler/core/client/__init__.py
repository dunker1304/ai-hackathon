"""HTTP / browser transport layer.

Two interchangeable implementations of `BaseClient`:
  * `HttpClient`     - plain httpx, for internal JSON APIs (fast, cheap)
  * `CamoufoxClient` - anti-detect headless Firefox, for walled HTML pages
"""

from app.crawler.core.client.base import BaseClient, FetchResponse
from app.crawler.core.client.browser import BrowserPool, CamoufoxClient
from app.crawler.core.client.proxy import Proxy, ProxyPool
from app.crawler.core.client.retry import RetryPolicy

__all__ = [
    "BaseClient",
    "BrowserPool",
    "CamoufoxClient",
    "FetchResponse",
    "Proxy",
    "ProxyPool",
    "RetryPolicy",
]
