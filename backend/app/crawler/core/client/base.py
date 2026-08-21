"""BaseClient ABC: get/post/get_json contract, injected session + rate limiter + retry policy."""

from __future__ import annotations

import json

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from app.crawler.core.exceptions import ParseError

if TYPE_CHECKING:
    from types import TracebackType

    from app.crawler.core.types import Headers, JSONDict, QueryParams


@dataclass(slots=True)
class FetchResponse:
    """Uniform response returned by every client (HTTP or browser-backed)."""

    url: str
    status: int
    text: str
    headers: Headers = field(default_factory=dict)
    elapsed: float = 0.0
    from_browser: bool = False
    # JSON payloads sniffed from XHR while the page loaded (browser clients).
    captured: list[JSONDict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ParseError(f"Response is not valid JSON: {exc}", url=self.url) from exc


class BaseClient(ABC):
    """Transport contract shared by HttpClient and CamoufoxClient.

    Implementations own their own lifecycle and must be usable as an async
    context manager so crawlers never leak sockets or browser processes.
    """

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @abstractmethod
    async def start(self) -> None:
        """Acquire underlying resources (http session / browser process)."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources. Must be idempotent."""

    @abstractmethod
    async def get(
        self,
        url: str,
        *,
        params: QueryParams | None = None,
        headers: Headers | None = None,
        **kwargs: Any,
    ) -> FetchResponse:
        """Fetch a URL and return its rendered/raw body."""

    async def get_json(
        self,
        url: str,
        *,
        params: QueryParams | None = None,
        headers: Headers | None = None,
        **kwargs: Any,
    ) -> Any:
        response = await self.get(url, params=params, headers=headers, **kwargs)
        return response.json()
