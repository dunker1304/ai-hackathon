"""Fingerprint & locale rotation: builds the per-launch Camoufox identity
(os, screen constraints, locale, webgl pair, humanize, geoip)."""

from __future__ import annotations

import random

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.crawler.core.types import BrowserOS

# Rough desktop market share; keeps the OS mix believable across many sessions.
OS_WEIGHTS: dict[BrowserOS, float] = {"windows": 0.72, "macos": 0.20, "linux": 0.08}

COMMON_SCREENS: tuple[tuple[int, int], ...] = (
    (1920, 1080),
    (1536, 864),
    (1440, 900),
    (1366, 768),
    (2560, 1440),
)


@dataclass(slots=True)
class FingerprintSpec:
    """Resolved identity for one browser launch. Maps 1:1 onto Camoufox kwargs."""

    os: BrowserOS
    locale: str
    screen: tuple[int, int]
    humanize: bool | float = True
    # Camoufox warns that browser-level image blocking is itself a WAF signal.
    # Off by default; `block_heavy_resources` (route-level) is the safer saving.
    block_images: bool = False
    block_webrtc: bool = True
    fingerprint_preset: bool = True
    geoip: bool | str = False
    extra_config: dict[str, Any] = field(default_factory=dict)

    def to_camoufox_kwargs(self) -> dict[str, Any]:
        """Translate to `AsyncCamoufox(**kwargs)`.

        `screen` is passed as a browserforge `Screen` constraint when the
        dependency is importable; otherwise it is silently dropped so the
        launcher still works with a bare camoufox install.
        """
        kwargs: dict[str, Any] = {
            "os": self.os,
            "locale": self.locale,
            "humanize": self.humanize,
            "block_webrtc": self.block_webrtc,
            "fingerprint_preset": self.fingerprint_preset,
        }
        if self.block_images:
            kwargs["block_images"] = True
            kwargs["i_know_what_im_doing"] = True
        if self.geoip:
            kwargs["geoip"] = self.geoip
        if self.extra_config:
            kwargs["config"] = self.extra_config

        try:
            from browserforge.fingerprints import Screen  # type: ignore[import-not-found]
        except ImportError:
            return kwargs

        width, height = self.screen
        kwargs["screen"] = Screen(max_width=width, max_height=height)
        return kwargs


class FingerprintRotator:
    """Draws a new FingerprintSpec per launch, optionally pinned by a seed so a
    long-running session keeps a stable identity."""

    def __init__(
        self,
        *,
        allowed_os: list[BrowserOS] | None = None,
        locales: list[str] | None = None,
        humanize: bool | float = True,
        block_images: bool = True,
        geoip: bool = False,
    ) -> None:
        self.allowed_os = allowed_os or list(OS_WEIGHTS)
        self.locales = locales or ["en-US"]
        self.humanize = humanize
        self.block_images = block_images
        self.geoip = geoip

    def next(self, *, seed: str | None = None) -> FingerprintSpec:
        rng = random.Random(seed) if seed is not None else random
        weights = [OS_WEIGHTS.get(o, 0.1) for o in self.allowed_os]
        return FingerprintSpec(
            os=rng.choices(self.allowed_os, weights=weights, k=1)[0],
            locale=rng.choice(self.locales),
            screen=rng.choice(COMMON_SCREENS),
            humanize=self.humanize,
            block_images=self.block_images,
            geoip=self.geoip,
        )
