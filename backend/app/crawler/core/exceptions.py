"""CrawlerError hierarchy: ClientError, RateLimitedError, BlockedError, ParseError, NotFoundError."""

from __future__ import annotations


class CrawlerError(Exception):
    """Base class for every crawler failure."""

    retryable: bool = False

    def __init__(self, message: str, *, url: str | None = None, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.url = url
        self.context = context

    def __str__(self) -> str:
        parts = [self.message]
        if self.url:
            parts.append(f"url={self.url}")
        parts += [f"{k}={v!r}" for k, v in self.context.items()]
        return " | ".join(parts)


# --- transport layer -------------------------------------------------------


class ClientError(CrawlerError):
    """Any failure while performing a request."""


class TransportError(ClientError):
    """Connection reset / DNS / TLS / proxy tunnel failure."""

    retryable = True


class NavigationTimeoutError(ClientError):
    """Request or navigation exceeded its deadline."""

    retryable = True


class HTTPStatusError(ClientError):
    """Non-2xx response that is not covered by a more specific error."""

    def __init__(self, message: str, *, status: int, url: str | None = None, **context: object) -> None:
        super().__init__(message, url=url, status=status, **context)
        self.status = status
        self.retryable = status >= 500 or status == 408


class NotFoundError(ClientError):
    """404 / product removed / delisted."""


class RateLimitedError(ClientError):
    """429 or marketplace-specific throttle response."""

    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None, **context: object) -> None:
        super().__init__(message, retry_after=retry_after, **context)
        self.retry_after = retry_after


class BlockedError(ClientError):
    """Anti-bot wall: captcha, "Robot Check", Cloudflare interstitial, empty
    sentinel payload. Signals proxy/session rotation, not a plain retry."""

    retryable = True


# --- browser layer ---------------------------------------------------------


class BrowserError(ClientError):
    """Camoufox/Playwright level failure (launch, context, page crash)."""

    retryable = True


class BrowserLaunchError(BrowserError):
    """Browser binary missing or failed to start."""

    retryable = False


class BrowserPoolExhaustedError(BrowserError):
    """No context could be acquired before the pool timeout."""


# --- parsing layer ---------------------------------------------------------


class ParseError(CrawlerError):
    """Selector missing / schema drift / unexpected payload shape."""


class ValidationError(CrawlerError):
    """Parsed object failed schema validation."""
