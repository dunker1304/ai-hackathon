"""Data-quality report for a batch of crawled products.

A crawl that returns rows is not the same as a crawl that returns *usable*
rows: during development one run reported 100% success while carrying prices
for 38% of products. The report exists so that failure mode is visible in the
API response and on the status page, not just in a script's stdout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.crawler.marketplaces.amazon.constants import REGION_CURRENCIES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from app.crawler.marketplaces.amazon.schemas import AmazonProduct

COVERAGE_FIELDS = ("title", "brand", "price", "rating", "review_count", "bought_past_month", "image_url")

LOW_COVERAGE = 0.8
LOW_VOLUME_COVERAGE = 0.3


def quality_report(
    products: Sequence[AmazonProduct],
    *,
    region: str = "us",
    location: str | None = None,
    failed: int = 0,
) -> tuple[dict[str, Any], list[str]]:
    """Return `(report, warnings)`.

    Warnings are phrased as instructions, because whoever reads them on the
    status page is usually not the person who wrote the crawler.
    """
    total = len(products)
    if not total:
        return {"products": 0, "failed": failed}, ["No products were parsed"]

    expected = REGION_CURRENCIES.get(region, "USD")
    where = location or "the IP default location"

    currencies: dict[str, int] = {}
    for product in products:
        if product.currency:
            currencies[product.currency] = currencies.get(product.currency, 0) + 1

    coverage = {
        field: round(sum(getattr(p, field) is not None for p in products) / total, 3) for field in COVERAGE_FIELDS
    }
    unshippable = sum(p.unshippable for p in products)

    report: dict[str, Any] = {
        "products": total,
        "failed": failed,
        "success_rate": round(total / (total + failed), 3) if total + failed else 0.0,
        "coverage": coverage,
        "with_bsr": round(sum(bool(p.best_seller_ranks) for p in products) / total, 3),
        "mean_confidence": round(sum(p.parse_confidence for p in products) / total, 3),
        "currencies": currencies,
        "unshippable": unshippable,
        "expected_currency": expected,
    }

    warnings: list[str] = []

    if set(currencies) - {expected}:
        warnings.append(
            f"Prices are not all in {expected} ({currencies}). The delivery location did not take "
            f"effect, so Amazon is quoting the currency of the exit IP; these figures cannot be "
            f"compared with other marketplaces."
        )
    if len(currencies) > 1:
        warnings.append("Mixed currencies in one batch — do not aggregate these prices.")

    if unshippable:
        warnings.append(
            f"{unshippable} product(s) have no buybox because they do not ship to {where}. "
            f"Their price is unknown, not zero. Try a different delivery location."
        )

    if coverage["price"] < LOW_COVERAGE and not unshippable:
        warnings.append(
            f"Price coverage is {coverage['price']:.0%} with no shipping blocks — the buybox "
            f"selector may have drifted."
        )

    if coverage["bought_past_month"] < LOW_VOLUME_COVERAGE:
        warnings.append(
            "Few listings expose 'bought in past month'; Amazon hides it for low-volume products, "
            "so demand has to be derived from best-seller rank and review count instead."
        )

    return report, warnings
