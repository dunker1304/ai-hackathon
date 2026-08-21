"""AmazonBaseClient: shared domain/locale, cookies, captcha detection, common request wrapper."""

from __future__ import annotations

import logging
import re

from typing import TYPE_CHECKING

from app.crawler.core.client.browser.client import CamoufoxClient
from app.crawler.core.exceptions import CrawlerError
from app.crawler.marketplaces.amazon import constants as const
from app.crawler.marketplaces.amazon.location import DeliveryLocation, resolve_location

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page

    from app.crawler.core.client.browser.pool import BrowserSlot

logger = logging.getLogger(__name__)

#: Sets the delivery location through Amazon's "glow" widget. Runs inside the
#: page so it inherits the session cookies and the CSRF token.
#:
#: Note the modern JSON endpoint (/portal-migration/hz/glow/address-change)
#: answers 200 but does not apply; only this legacy form-encoded path works.
_SET_DELIVERY_JS = """
async ([zip, endpoint]) => {
    const tokenEl = document.querySelector('input[name="anti-csrftoken-a2z"]');
    const match = document.body.innerHTML.match(/"CSRF_TOKEN"\\s*:\\s*"([^"]+)"/);
    const token = tokenEl ? tokenEl.value : (match ? match[1] : '');
    const body = new URLSearchParams({
        locationType: 'LOCATION_INPUT',
        zipCode: zip,
        storeContext: 'generic',
        deviceType: 'web',
        pageType: 'Detail',
        actionSource: 'glow',
    });
    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'anti-csrftoken-a2z': token,
            },
            body,
        });
        if (!res.ok) return {ok: false, reason: 'http ' + res.status};
        const text = await res.text();
        try {
            const json = JSON.parse(text);
            return {ok: json.isAddressUpdated === 1, reason: text.slice(0, 120)};
        } catch (e) {
            return {ok: false, reason: 'unparseable: ' + text.slice(0, 80)};
        }
    } catch (e) {
        return {ok: false, reason: String(e).slice(0, 120)};
    }
}
"""


class AmazonLocationError(CrawlerError):
    """The delivery location could not be applied.

    Fatal by default: without it Amazon hides the buybox on most listings and
    the crawl would quietly produce rows with `price: null`.
    """


class AmazonBaseClient(CamoufoxClient):
    """Camoufox client pre-configured for Amazon.

    Four things on top of the generic client:

    * **Currency pinning** - Amazon renders prices in the currency of the exit
      IP. From a VN address a mug costs "VND 913,570", which silently corrupts
      every revenue metric. `i18n-prefs` / `lc-main` force USD + en_US.
    * **Delivery location** - currency cookies alone are not enough: with an
      address outside the storefront's country Amazon suppresses the buybox
      ("cannot be shipped to your selected delivery location") and the price
      element is simply absent. Measured price coverage was 38% before this,
      100% after. The location is applied once per browser slot and then
      **verified** against the glow widget, because the endpoint returns
      success for postcodes it never actually applies.
    * **Block markers** - Akamai answers with HTTP 200 and a `bm-verify`
      interstitial, so status codes alone never reveal a block.
    * **Overlay dismissal** - region/cookie modals cover the content.
    """

    block_markers: tuple[re.Pattern[str], ...] = (
        re.compile(r"Enter the characters you see below", re.IGNORECASE),
        re.compile(r"Sorry, we just need to make sure you're not a robot", re.IGNORECASE),
        re.compile(r"To discuss automated access to Amazon data", re.IGNORECASE),
    )

    overlay_selectors: tuple[str, ...] = (
        "input[data-action-type='DISMISS']",  # delivery-location modal
        "#sp-cc-accept",  # cookie banner (EU storefronts)
        "button[data-action='a-popover-close']",
    )

    def __init__(
        self,
        *,
        region: str = const.DEFAULT_REGION,
        location: str | DeliveryLocation | None = None,
        strict_location: bool = True,
        **kwargs: object,
    ) -> None:
        """`location` accepts a preset name ("los-angeles"), a postcode
        ("90210"), `"none"` to disable, or a `DeliveryLocation`. `None` picks
        the storefront default.

        With `strict_location=True` (the default) a location that cannot be
        applied aborts the crawl instead of yielding priceless rows.
        """
        if region not in const.BASE_URLS:
            raise ValueError(f"Unknown Amazon region {region!r}; known: {sorted(const.BASE_URLS)}")

        self.region = region
        self.location = location if isinstance(location, DeliveryLocation) else resolve_location(region, location)
        self.strict_location = strict_location
        #: Slots whose context already carries a verified delivery address.
        self._located_slots: set[int] = set()
        super().__init__(**kwargs)  # type: ignore[arg-type]

    @property
    def base_url(self) -> str:
        return const.BASE_URLS[self.region]

    @property
    def delivery_zip(self) -> str | None:
        return self.location.zip_code if self.location else None

    async def prepare_page(self, page: Page, slot: BrowserSlot) -> None:
        await super().prepare_page(page, slot)
        if self.region == "us":
            await page.context.add_cookies(const.US_LOCALE_COOKIES)  # type: ignore[arg-type]

    async def after_navigate(self, page: Page, slot: BrowserSlot) -> None:
        await super().after_navigate(page, slot)
        await self.ensure_delivery_location(page, slot)

    async def read_glow(self, page: Page) -> str | None:
        """Whatever the delivery widget currently displays."""
        try:
            node = await page.query_selector(const.GLOW_INGRESS_SELECTOR)
            return (await node.inner_text()).strip() if node else None
        except Exception:
            return None

    async def ensure_delivery_location(self, page: Page, slot: BrowserSlot) -> bool:
        """Apply and verify the delivery location once per browser slot.

        Returns True when the page was reloaded (the DOM changed).

        Verification is the important half: Amazon's endpoint reports
        `isAddressUpdated: 1` for postcodes it silently ignores -- e.g. a UK
        postcode on amazon.com -- so the glow widget is re-read and compared
        against what the location expects.
        """
        if self.location is None or slot.index in self._located_slots:
            return False

        result = await page.evaluate(_SET_DELIVERY_JS, [self.location.zip_code, const.GLOW_ENDPOINT])
        if not result.get("ok"):
            return self._location_failed(
                f"Amazon rejected {self.location.zip_code} on {self.region}: {result.get('reason')}"
            )

        await page.reload(wait_until="domcontentloaded", timeout=self.timeout)

        glow = await self.read_glow(page)
        if not self.location.matches(glow):
            return self._location_failed(
                f"Delivery location did not stick on {self.region}: asked for "
                f"{self.location.zip_code} ({self.location.label}), widget shows {glow!r}. "
                f"Prices will be missing or in the wrong currency."
            )

        self._located_slots.add(slot.index)
        logger.info(
            "Delivery location %s (%s) confirmed on slot %d: %r",
            self.location.zip_code,
            self.location.label,
            slot.index,
            glow,
        )
        return True

    def _location_failed(self, message: str) -> bool:
        if self.strict_location:
            raise AmazonLocationError(message)
        logger.warning("%s -- continuing with degraded price coverage", message)
        return False
