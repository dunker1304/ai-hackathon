"""Seed the Product Opportunity Hub tables from backend/data/ artifacts.

Idempotent: truncates hub tables and reloads. The ADAPTERS dict is the
swap-in seam for real BTC data — drop a file in backend/data/ with the same
column names and re-run. Amazon data comes from scripts/crawl_amazon.py
(ScraperAPI); Etsy still expects the Alura export column layout.

Run from backend/:  uv run python scripts/seed.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app import scoring
from app.db import SessionLocal
from app.models import Keyword, Listing, ProductType, TaxonomyAlias, TrendPoint

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EMBED_BATCH_SIZE = 100

# Column adapters: map a source export's columns onto the listings table.
# To ingest a real export, keep the same file name + column names, done.
ADAPTERS = {
    "alura_etsy": {
        "file": "listings_etsy.csv",
        "source": "etsy",
        "columns": {
            "title": "Listing Title",
            "price": "Price",
            "est_sales": "Est. Mo. Sales",
            "favorites": "Favorites",
            "review_count": None,  # Alura exports don't include reviews
            "shop": "Shop Name",
            "tags": "Tags",
            "url": "URL",
        },
    },
    # Produced by scripts/crawl_amazon.py (ScraperAPI structured Amazon
    # search). Same column layout as the old Helium10 export; "Sales" is the
    # "N+ bought in past month" badge, 0 when Amazon shows none.
    "scraperapi_amazon": {
        "file": "listings_amazon.csv",
        "source": "amazon",
        "columns": {
            "title": "Title",
            "price": "Price",
            "est_sales": "Sales",
            "favorites": None,  # Amazon has no favorites
            "review_count": "Review Count",
            "shop": "Seller",
            "tags": None,
            "url": "URL",
        },
    },
}


def load_taxonomy(db) -> list[dict]:
    rows = json.loads((DATA_DIR / "taxonomy.json").read_text())
    for r in rows:
        db.add(
            ProductType(
                id=r["id"],
                name=r["name"],
                category=r["category"],
                material=r["material"],
                difficulty=r["difficulty"],
                margin_min=r["margin_min"],
                margin_max=r["margin_max"],
                personalization_friendly=r["personalization_friendly"],
                fit=scoring.compute_fit(r["difficulty"], r["margin_min"], r["margin_max"]),
                seasonality=r["seasonality"],
            )
        )
    db.commit()
    return rows


def load_aliases(db) -> None:
    """Embed every alias and store in the multi-vector index. Skips (with a
    loud warning) when the embeddings API is unavailable — normalization then
    falls back to lexical matching."""
    aliases: dict[str, list[str]] = json.loads((DATA_DIR / "aliases.json").read_text())
    pairs = [(pt, alias) for pt, arr in aliases.items() for alias in arr]

    try:
        from app.llm import get_embeddings

        embedder = get_embeddings()
        for i in range(0, len(pairs), EMBED_BATCH_SIZE):
            batch = pairs[i : i + EMBED_BATCH_SIZE]
            vectors = embedder.embed_documents([alias for _, alias in batch])
            for (pt, alias), vec in zip(batch, vectors):
                db.add(TaxonomyAlias(product_type_id=pt, alias=alias, embedding=vec))
            db.commit()
            print(f"  embedded {min(i + EMBED_BATCH_SIZE, len(pairs))}/{len(pairs)} aliases")
    except Exception as exc:  # noqa: BLE001 - keep seeding usable without API keys
        db.rollback()
        print(f"  WARNING: embeddings unavailable ({str(exc)[:80]}...)")
        print("  Aliases NOT embedded — normalization will use the lexical fallback.")
        print("  Fix OPENAI_API_KEY and re-run scripts/seed.py to enable vector retrieval.")


def load_listings(db, now: datetime) -> None:
    rng_offsets = {"etsy": 2, "amazon": 7}  # staggered freshness hours per source
    for adapter in ADAPTERS.values():
        path = DATA_DIR / adapter["file"]
        cols = adapter["columns"]
        fetched_at = now - timedelta(hours=rng_offsets[adapter["source"]])
        count = 0
        with open(path) as f:
            for row in csv.DictReader(f):
                pt = row.get("_pt") or None
                tags = (row.get(cols["tags"]) or "").split(",") if cols["tags"] else []
                db.add(
                    Listing(
                        source=adapter["source"],
                        url=row.get(cols["url"]) or None,
                        title=row[cols["title"]],
                        price=float(row[cols["price"]]),
                        favorites=int(row[cols["favorites"]] or 0) if cols["favorites"] else 0,
                        est_sales=int(float(row[cols["est_sales"]] or 0)),
                        review_count=int(row[cols["review_count"]] or 0) if cols["review_count"] else 0,
                        shop=row[cols["shop"]],
                        tags=[t for t in tags if t],
                        fetched_at=fetched_at,
                        # Mock rows carry ground-truth labels; real exports won't,
                        # and get normalized lazily via the pipeline instead.
                        product_type_id=pt,
                        norm_confidence=0.99 if pt else None,
                    )
                )
                count += 1
        db.commit()
        print(f"  {adapter['file']}: {count} listings ({adapter['source']})")


def load_keywords(db, now: datetime) -> None:
    with open(DATA_DIR / "keywords.csv") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        db.add(
            Keyword(
                keyword=row["keyword"],
                source=row["source"],
                volume=int(row["volume"]),
                competition=float(row["competition"]),
                cpc=float(row["cpc"]),
                trend_30d=float(row["trend_30d"]),
                product_type_id=row["product_type_id"] or None,
                fetched_at=now - timedelta(hours=4),
            )
        )
    db.commit()
    print(f"  keywords.csv: {len(rows)} keywords")


def load_trends(db) -> None:
    with open(DATA_DIR / "trends.csv") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        db.add(
            TrendPoint(
                entity=row["entity"],
                entity_type=row["entity_type"],
                source="google_trends",
                date=datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc),
                value=float(row["value"]),
            )
        )
    db.commit()
    print(f"  trends.csv: {len(rows)} trend points")


def main() -> None:
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        print("truncating hub tables...")
        db.execute(
            text(
                "TRUNCATE scores, trends, keywords, listings, taxonomy_aliases, taxonomy "
                "RESTART IDENTITY CASCADE"
            )
        )
        db.commit()

        print("loading taxonomy...")
        load_taxonomy(db)
        print("embedding aliases...")
        load_aliases(db)
        print("loading listings...")
        load_listings(db, now)
        print("loading keywords...")
        load_keywords(db, now)
        print("loading trends...")
        load_trends(db)

        print("computing opportunity scores...")
        written = scoring.compute_all(db, now)
        print(f"done: {written} product types scored")
    finally:
        db.close()


if __name__ == "__main__":
    main()
