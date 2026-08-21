"""Proxy pool: rotation strategy, health tracking, ban/cooldown on BlockedError."""

from __future__ import annotations

import asyncio
import itertools
import logging
import random
import time

from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Proxy:
    """A single upstream proxy. `server` is the Playwright/httpx form
    (e.g. "http://gw.provider.io:8000"); credentials stay separate."""

    server: str
    username: str | None = None
    password: str | None = None

    failures: int = 0
    successes: int = 0
    banned_until: float = 0.0

    @classmethod
    def parse(cls, raw: str) -> Proxy:
        """Accepts `http://user:pass@host:port`, `host:port`, `host:port:user:pass`."""
        raw = raw.strip()
        if "://" in raw:
            u = urlparse(raw)
            server = f"{u.scheme}://{u.hostname}"
            if u.port:
                server += f":{u.port}"
            return cls(server=server, username=u.username, password=u.password)

        parts = raw.split(":")
        if len(parts) == 2:
            return cls(server=f"http://{parts[0]}:{parts[1]}")
        if len(parts) == 4:
            host, port, user, pwd = parts
            return cls(server=f"http://{host}:{port}", username=user, password=pwd)
        raise ValueError(f"Unrecognized proxy format: {raw!r}")

    @property
    def is_banned(self) -> bool:
        return time.monotonic() < self.banned_until

    @property
    def health(self) -> float:
        total = self.successes + self.failures
        return 1.0 if total == 0 else self.successes / total

    def as_playwright(self) -> dict[str, str]:
        cfg = {"server": self.server}
        if self.username:
            cfg["username"] = self.username
        if self.password:
            cfg["password"] = self.password
        return cfg

    def as_url(self) -> str:
        """httpx-compatible proxy URL with inline credentials."""
        if not self.username:
            return self.server
        scheme, _, rest = self.server.partition("://")
        return f"{scheme}://{self.username}:{self.password or ''}@{rest}"


@dataclass
class ProxyPool:
    """Rotating pool with per-proxy ban cooldown.

    strategy: "round_robin" | "random" | "healthiest"
    """

    proxies: list[Proxy] = field(default_factory=list)
    strategy: str = "round_robin"
    ban_seconds: float = 300.0
    max_failures: int = 3

    _cycle: itertools.cycle | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._cycle = itertools.cycle(range(len(self.proxies))) if self.proxies else None

    @classmethod
    def from_lines(cls, raw: str | list[str] | None, **kwargs: object) -> ProxyPool:
        """Build from env value: newline/comma separated proxy strings."""
        if not raw:
            return cls(proxies=[], **kwargs)  # type: ignore[arg-type]
        lines = raw.replace(",", "\n").splitlines() if isinstance(raw, str) else raw
        return cls(proxies=[Proxy.parse(x) for x in lines if x.strip()], **kwargs)  # type: ignore[arg-type]

    @property
    def enabled(self) -> bool:
        return bool(self.proxies)

    def _available(self) -> list[Proxy]:
        return [p for p in self.proxies if not p.is_banned]

    async def acquire(self) -> Proxy | None:
        """Pick the next usable proxy, or None when the pool is empty/disabled."""
        if not self.proxies:
            return None

        async with self._lock:
            available = self._available()
            if not available:
                # everything is cooling down -> unban the one that recovers soonest
                soonest = min(self.proxies, key=lambda p: p.banned_until)
                soonest.banned_until = 0.0
                soonest.failures = 0
                logger.warning("All proxies banned; force-releasing %s", soonest.server)
                available = [soonest]

            if self.strategy == "random":
                return random.choice(available)
            if self.strategy == "healthiest":
                return max(available, key=lambda p: (p.health, -p.failures))

            assert self._cycle is not None
            for _ in range(len(self.proxies)):
                candidate = self.proxies[next(self._cycle)]
                if not candidate.is_banned:
                    return candidate
            return available[0]

    def report_success(self, proxy: Proxy | None) -> None:
        if proxy is None:
            return
        proxy.successes += 1
        proxy.failures = 0

    def report_failure(self, proxy: Proxy | None, *, ban: bool = False) -> None:
        if proxy is None:
            return
        proxy.failures += 1
        if ban or proxy.failures >= self.max_failures:
            proxy.banned_until = time.monotonic() + self.ban_seconds
            logger.warning("Proxy %s banned for %.0fs", proxy.server, self.ban_seconds)
