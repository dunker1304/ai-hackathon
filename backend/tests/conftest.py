from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def amazon_serp_html() -> str:
    """A real Amazon SERP captured with scripts/test_camoufox.py.

    Keyword: "amazon personalized sweatshirts", 48 organic results, page 1 of 3.
    """
    return (FIXTURES / "amazon_search_page1.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def amazon_detail_html() -> str:
    """A real Amazon /dp page (B0721C21RJ, Hanes EcoSmart sweatshirt).

    Captured from a VN IP *before* the delivery-location fix, so it prices in
    VND. That is deliberate: it exercises the currency-mismatch path that the
    live crawler must never hit silently.
    """
    return (FIXTURES / "amazon_product_detail.html").read_text(encoding="utf-8")
