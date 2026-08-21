"""Camoufox process lifecycle: builds launch options and owns the AsyncCamoufox
browser handle. One launcher == one browser process."""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.crawler.core.client.browser.fingerprint import FingerprintRotator, FingerprintSpec
from app.crawler.core.exceptions import BrowserLaunchError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Browser

    from app.crawler.core.client.proxy import Proxy

logger = logging.getLogger(__name__)

#: Camoufox aborts the launch when a generated fingerprint is internally
#: inconsistent (most often an unsupported WebGL vendor/renderer pair).
FINGERPRINT_ERROR_MARKERS = ("webgl", "fingerprint", "no data found for", "not supported for")


def _is_fingerprint_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in FINGERPRINT_ERROR_MARKERS)


@dataclass(slots=True)
class LaunchOptions:
    """Everything that must be decided *before* the browser process starts."""

    headless: bool | str = True  # True | False | "virtual" (Xvfb on Linux)
    proxy: Proxy | None = None
    fingerprint: FingerprintSpec | None = None
    user_data_dir: str | None = None
    persistent_context: bool = False
    enable_cache: bool = False
    disable_coop: bool = False
    addons: list[str] | None = None
    main_world_eval: bool = False
    extra: dict[str, Any] | None = None

    def to_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "enable_cache": self.enable_cache,
            "main_world_eval": self.main_world_eval,
        }
        if self.fingerprint is not None:
            kwargs.update(self.fingerprint.to_camoufox_kwargs())
        if self.proxy is not None:
            kwargs["proxy"] = self.proxy.as_playwright()
            # Match geolocation/timezone/locale to the exit IP.
            kwargs.setdefault("geoip", True)
        if self.disable_coop:
            kwargs["disable_coop"] = True
        if self.addons:
            kwargs["addons"] = self.addons
        if self.persistent_context:
            if not self.user_data_dir:
                raise BrowserLaunchError("persistent_context=True requires user_data_dir")
            kwargs["persistent_context"] = True
            kwargs["user_data_dir"] = self.user_data_dir
        if self.extra:
            kwargs.update(self.extra)
        return kwargs


class CamoufoxLauncher:
    """Starts/stops a single Camoufox (Firefox) process via AsyncCamoufox.

        launcher = CamoufoxLauncher(rotator=FingerprintRotator())
        browser = await launcher.start(proxy=proxy)
        ...
        await launcher.stop()

    `AsyncCamoufox` is an async context manager; we drive it manually with
    `__aenter__` / `__aexit__` so the browser can outlive a single `with` block
    and be shared by a pool.
    """

    def __init__(
        self,
        *,
        headless: bool | str = True,
        rotator: FingerprintRotator | None = None,
        base_options: LaunchOptions | None = None,
    ) -> None:
        self.headless = headless
        self.rotator = rotator or FingerprintRotator()
        self.base_options = base_options
        self._cm: Any = None
        self._browser: Browser | None = None
        self._options: LaunchOptions | None = None

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise BrowserLaunchError("Launcher has not been started")
        return self._browser

    @property
    def running(self) -> bool:
        return self._browser is not None

    @property
    def options(self) -> LaunchOptions | None:
        return self._options

    def build_options(self, *, proxy: Proxy | None = None, seed: str | None = None) -> LaunchOptions:
        base = self.base_options
        return LaunchOptions(
            headless=base.headless if base else self.headless,
            proxy=proxy,
            fingerprint=self.rotator.next(seed=seed),
            user_data_dir=base.user_data_dir if base else None,
            persistent_context=base.persistent_context if base else False,
            enable_cache=base.enable_cache if base else False,
            disable_coop=base.disable_coop if base else False,
            addons=base.addons if base else None,
            main_world_eval=base.main_world_eval if base else False,
            extra=base.extra if base else None,
        )

    async def start(
        self,
        *,
        proxy: Proxy | None = None,
        seed: str | None = None,
        max_attempts: int = 3,
    ) -> Browser:
        """Launch the browser, redrawing the fingerprint if Camoufox rejects it.

        Some BrowserForge/preset draws produce an unsupported WebGL
        vendor/renderer pair for the host GPU; Camoufox refuses to launch rather
        than leak. Retrying with a new draw is the documented workaround.
        """
        if self._browser is not None:
            return self._browser

        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:  # pragma: no cover - env issue
            raise BrowserLaunchError(
                "camoufox is not installed. Run `uv add camoufox[geoip]` then `python -m camoufox fetch`."
            ) from exc

        options = self.base_options or self.build_options(proxy=proxy, seed=seed)
        if options.proxy is None and proxy is not None:
            options.proxy = proxy

        last: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            if options.fingerprint is None or attempt > 1:
                options.fingerprint = self.rotator.next(seed=None if attempt > 1 else seed)

            kwargs = options.to_kwargs()
            logger.info(
                "Launching Camoufox (os=%s locale=%s headless=%s proxy=%s attempt=%d)",
                kwargs.get("os"),
                kwargs.get("locale"),
                kwargs.get("headless"),
                options.proxy.server if options.proxy else None,
                attempt,
            )

            try:
                self._cm = AsyncCamoufox(**kwargs)
                self._browser = await self._cm.__aenter__()
            except Exception as exc:
                last = exc
                self._cm = None
                self._browser = None
                if not _is_fingerprint_error(exc) or attempt == max_attempts:
                    raise BrowserLaunchError(f"Failed to launch Camoufox: {exc}") from exc
                logger.warning("Bad fingerprint draw (attempt %d/%d): %s", attempt, max_attempts, exc)
            else:
                self._options = options
                return self._browser

        raise BrowserLaunchError(f"Failed to launch Camoufox: {last}")

    async def stop(self) -> None:
        if self._cm is None:
            return
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:
            logger.exception("Error while closing Camoufox")
        finally:
            self._cm = None
            self._browser = None
            self._options = None

    async def restart(self, *, proxy: Proxy | None = None, seed: str | None = None) -> Browser:
        """Full rotation: kill the process and relaunch with a fresh identity."""
        await self.stop()
        self.base_options = None
        return await self.start(proxy=proxy, seed=seed)
