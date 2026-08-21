"""Unit tests for the pure transforms in scripts/crawl_amazon.py.

Network fetching is not tested here — every function under test is
deterministic and offline.
"""

from __future__ import annotations

from scripts.crawl_amazon import (
    build_queries,
    dedupe_rows,
    parse_bought_count,
    row_from_result,
)

# --- parse_bought_count -----------------------------------------------------


def test_parse_bought_count_returns_zero_for_missing_message() -> None:
    assert parse_bought_count(None) == 0
    assert parse_bought_count("") == 0


def test_parse_bought_count_returns_zero_for_unrelated_text() -> None:
    assert parse_bought_count("Limited time deal") == 0


def test_parse_bought_count_plain_number() -> None:
    assert parse_bought_count("500+ bought in past month") == 500


def test_parse_bought_count_k_suffix() -> None:
    assert parse_bought_count("2K+ bought in past month") == 2000


def test_parse_bought_count_decimal_k_suffix() -> None:
    assert parse_bought_count("1.5K+ bought in past month") == 1500


def test_parse_bought_count_m_suffix() -> None:
    assert parse_bought_count("3M+ bought in past month") == 3_000_000


def test_parse_bought_count_weekly_message_scaled_to_month() -> None:
    assert parse_bought_count("50+ bought in past week") == 200


# --- row_from_result --------------------------------------------------------


def _result(**overrides: object) -> dict:
    base: dict = {
        "asin": "B0TEST1234",
        "name": "Personalized Acrylic Ornament - Christmas Gift",
        "price": 25.99,
        "price_string": "$25.99",
        "total_reviews": 150,
        "purchase_history_message": "2K+ bought in past month",
        "url": "https://www.amazon.com/dp/B0TEST1234",
    }
    return {**base, **overrides}


def test_row_from_result_maps_all_columns() -> None:
    row = row_from_result(_result())

    assert row == {
        "Title": "Personalized Acrylic Ornament - Christmas Gift",
        "Price": 25.99,
        "Sales": 2000,
        "Review Count": 150,
        "Seller": "",
        "URL": "https://www.amazon.com/dp/B0TEST1234",
    }


def test_row_from_result_returns_none_when_title_missing() -> None:
    assert row_from_result(_result(name=None)) is None
    assert row_from_result({}) is None


def test_row_from_result_falls_back_to_price_string() -> None:
    row = row_from_result(_result(price=None, price_string="$1,299.00"))

    assert row is not None
    assert row["Price"] == 1299.0


def test_row_from_result_returns_none_when_price_unparseable() -> None:
    assert row_from_result(_result(price=None, price_string=None)) is None
    assert row_from_result(_result(price=None, price_string="N/A")) is None


def test_row_from_result_parses_review_count_with_commas() -> None:
    row = row_from_result(_result(total_reviews="1,234"))

    assert row is not None
    assert row["Review Count"] == 1234


def test_row_from_result_defaults_missing_optional_fields() -> None:
    row = row_from_result(
        _result(total_reviews=None, purchase_history_message=None, url=None)
    )

    assert row is not None
    assert row["Sales"] == 0
    assert row["Review Count"] == 0
    assert row["URL"] == ""


# --- dedupe_rows ------------------------------------------------------------


def test_dedupe_rows_drops_duplicate_urls_keeping_first() -> None:
    first = {"Title": "A", "URL": "https://www.amazon.com/dp/B0AAA"}
    dup = {"Title": "A again", "URL": "https://www.amazon.com/dp/B0AAA"}
    other = {"Title": "B", "URL": "https://www.amazon.com/dp/B0BBB"}

    assert dedupe_rows([first, dup, other]) == [first, other]


def test_dedupe_rows_keeps_rows_without_url() -> None:
    a = {"Title": "A", "URL": ""}
    b = {"Title": "B", "URL": ""}

    assert dedupe_rows([a, b]) == [a, b]


# --- build_queries ----------------------------------------------------------


def test_build_queries_uses_taxonomy_names() -> None:
    taxonomy = [
        {"id": "acrylic-ornament", "name": "Custom Shape Acrylic Ornament"},
        {"id": "ceramic-mug", "name": "Ceramic Mug 11oz"},
    ]

    assert build_queries(taxonomy) == [
        "Custom Shape Acrylic Ornament",
        "Ceramic Mug 11oz",
    ]


def test_build_queries_skips_entries_without_name() -> None:
    assert build_queries([{"id": "x"}, {"name": "Tumbler"}]) == ["Tumbler"]
