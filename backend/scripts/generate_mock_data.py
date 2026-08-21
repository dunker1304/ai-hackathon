"""One-time mock data generator for the Product Opportunity Hub.

Produces committed artifacts in backend/data/ so demo-day setup needs zero
LLM calls. The taxonomy is authored in-code (deterministic); seller-style
aliases are generated once via LLM (with a deterministic template fallback);
listings/keywords/trends are pure seeded numpy so score percentiles spread
realistically.

Run from backend/:  uv run python scripts/generate_mock_data.py
Swap-in for real BTC data: replace the CSVs with real Alura/Helium10 exports
(same column names) and re-run scripts/seed.py — nothing else changes.
"""

from __future__ import annotations

import csv
import json
import sys

from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RNG_SEED = 42

# --- Printway-style taxonomy (mock; swap for the real BTC CSV later) ---
# (id, name, category, material, difficulty 1-5, margin_min, margin_max,
#  personalization_friendly, peak_month 1-12, peak_label, seasonal_amplitude 0-1)
TAXONOMY: list[tuple] = [
    ("acrylic-ornament", "Custom Shape Acrylic Ornament", "Ornaments", "acrylic", 2, 0.45, 0.65, True, 12, "Christmas", 0.9),
    ("wood-ornament", "Engraved Wood Ornament", "Ornaments", "wood", 2, 0.40, 0.60, True, 12, "Christmas", 0.9),
    ("metal-ornament", "Stamped Metal Ornament", "Ornaments", "metal", 3, 0.35, 0.55, True, 12, "Christmas", 0.85),
    ("ceramic-ornament", "Ceramic Photo Ornament", "Ornaments", "ceramic", 3, 0.35, 0.55, True, 12, "Christmas", 0.9),
    ("acrylic-suncatcher", "Acrylic Suncatcher", "Home Decor", "acrylic", 2, 0.45, 0.65, True, 5, "Mother's Day", 0.5),
    ("stainless-tumbler", "Stainless Steel Tumbler 20oz", "Drinkware", "metal", 2, 0.35, 0.55, True, 6, "Father's Day", 0.4),
    ("ceramic-mug", "Ceramic Mug 11oz", "Drinkware", "ceramic", 1, 0.40, 0.60, True, 12, "Christmas", 0.5),
    ("glass-can", "Beer Can Glass", "Drinkware", "glass", 1, 0.40, 0.60, True, 6, "Father's Day", 0.35),
    ("wood-cutting-board", "Engraved Cutting Board", "Kitchen", "wood", 2, 0.35, 0.55, True, 12, "Christmas", 0.6),
    ("coir-doormat", "Custom Coir Doormat", "Home Decor", "fabric", 2, 0.30, 0.50, True, 11, "Holiday hosting", 0.45),
    ("fleece-blanket", "Personalized Fleece Blanket", "Home Decor", "fabric", 1, 0.35, 0.55, True, 12, "Christmas", 0.7),
    ("sherpa-blanket", "Sherpa Photo Blanket", "Home Decor", "fabric", 2, 0.30, 0.50, True, 12, "Christmas", 0.75),
    ("canvas-print", "Custom Canvas Print", "Wall Art", "canvas", 1, 0.45, 0.70, True, 12, "Christmas", 0.55),
    ("paper-poster", "Custom Art Poster", "Wall Art", "paper", 1, 0.50, 0.70, True, 12, "Christmas", 0.4),
    ("metal-sign", "Custom Metal Sign", "Wall Art", "metal", 3, 0.30, 0.50, True, 6, "Patio season", 0.4),
    ("wood-sign", "Personalized Wood Sign", "Wall Art", "wood", 2, 0.35, 0.55, True, 5, "Wedding season", 0.45),
    ("neon-sign", "Custom Neon LED Sign", "Lighting", "acrylic", 4, 0.25, 0.45, True, 5, "Wedding season", 0.4),
    ("acrylic-night-light", "3D Acrylic Night Light", "Lighting", "acrylic", 3, 0.40, 0.60, True, 12, "Christmas", 0.6),
    ("wood-photo-frame", "Engraved Photo Frame", "Keepsakes", "wood", 2, 0.35, 0.55, True, 5, "Mother's Day", 0.5),
    ("acrylic-keychain", "Custom Acrylic Keychain", "Accessories", "acrylic", 1, 0.50, 0.70, True, 12, "Christmas", 0.35),
    ("metal-keychain", "Engraved Metal Keychain", "Accessories", "metal", 1, 0.45, 0.65, True, 6, "Father's Day", 0.4),
    ("leather-keychain", "Personalized Leather Keychain", "Accessories", "leather", 2, 0.40, 0.60, True, 6, "Father's Day", 0.4),
    ("garden-stone", "Memorial Garden Stone", "Garden & Outdoor", "stone", 3, 0.30, 0.50, True, 5, "Spring", 0.55),
    ("wind-chime", "Memorial Wind Chime", "Garden & Outdoor", "metal", 3, 0.30, 0.50, True, 5, "Spring", 0.5),
    ("garden-flag", "Custom Garden Flag", "Garden & Outdoor", "fabric", 1, 0.40, 0.60, True, 4, "Spring", 0.6),
    ("pet-tag", "Engraved Pet ID Tag", "Pet", "metal", 1, 0.50, 0.70, True, 8, "Adoption season", 0.2),
    ("pet-bandana", "Custom Pet Bandana", "Pet", "fabric", 1, 0.45, 0.65, True, 7, "Summer", 0.3),
    ("pet-bowl", "Personalized Ceramic Pet Bowl", "Pet", "ceramic", 2, 0.35, 0.55, True, 12, "Christmas", 0.4),
    ("tshirt", "Custom Printed T-Shirt", "Apparel", "fabric", 1, 0.30, 0.50, True, 6, "Summer", 0.3),
    ("hoodie", "Custom Hoodie", "Apparel", "fabric", 1, 0.25, 0.45, True, 11, "Winter", 0.5),
    ("tote-bag", "Canvas Tote Bag", "Accessories", "canvas", 1, 0.40, 0.60, True, 8, "Back to school", 0.3),
    ("throw-pillow", "Custom Throw Pillow", "Home Decor", "fabric", 1, 0.40, 0.60, True, 12, "Christmas", 0.5),
    ("wood-plaque", "Engraved Wood Plaque", "Keepsakes", "wood", 2, 0.35, 0.55, True, 6, "Graduation", 0.55),
    ("crystal-plaque", "Crystal Award Plaque", "Keepsakes", "glass", 4, 0.25, 0.45, True, 6, "Graduation", 0.5),
    ("acrylic-plaque", "Acrylic Award Plaque", "Keepsakes", "acrylic", 3, 0.35, 0.55, True, 6, "Graduation", 0.5),
    ("car-charm", "Acrylic Car Hanging Charm", "Accessories", "acrylic", 1, 0.50, 0.70, True, 12, "Christmas", 0.4),
    ("bar-necklace", "Engraved Bar Necklace", "Jewelry", "metal", 3, 0.35, 0.55, True, 5, "Mother's Day", 0.55),
    ("bead-bracelet", "Custom Bead Bracelet", "Jewelry", "metal", 3, 0.35, 0.55, True, 2, "Valentine's Day", 0.6),
    ("wallet-card", "Engraved Wallet Insert Card", "Keepsakes", "metal", 1, 0.50, 0.70, True, 6, "Father's Day", 0.65),
    ("photo-puzzle", "Custom Photo Puzzle", "Toys & Games", "paper", 2, 0.40, 0.60, True, 12, "Christmas", 0.7),
    ("mouse-pad", "Custom Mouse Pad", "Office", "fabric", 1, 0.45, 0.65, True, 8, "Back to school", 0.35),
    ("desk-nameplate", "Wood Desk Name Plate", "Office", "wood", 2, 0.40, 0.60, True, 8, "Back to school", 0.3),
]

RECIPIENTS = ["Grandpa", "Grandma", "Mom", "Dad", "Best Friend", "Teacher", "Dog Mom", "Couple", "Sister", "Boss"]
OCCASIONS = ["Christmas", "Mother's Day", "Father's Day", "Wedding", "Anniversary", "Birthday", "Graduation", "Memorial", "Housewarming", "Valentine's Day"]
JUNK_PREFIX = ["", "", "Personalized ", "Custom ", "Handmade ", "Unique "]
JUNK_SUFFIX = ["", "", " - Free Shipping", " | Best Gift Idea", " Gift 2026", " Keepsake"]

# Listings deliberately OUTSIDE the catalog (~10%) so the demo can show
# honest "out of catalog" behaviour.
OUT_OF_CATALOG_TITLES = [
    "Resin Dice Set for DnD Tabletop Gaming",
    "Crochet Octopus Plush Toy Handmade Amigurumi",
    "Scented Soy Candle Lavender Vanilla 8oz",
    "3D Printed Articulated Dragon Fidget Toy",
    "Macrame Wall Hanging Boho Plant Holder",
    "Handmade Ceramic Vase Speckled Glaze",
    "Vintage Style Polymer Clay Earrings",
    "Digital Wedding Invitation Template Download",
    "Knitted Baby Booties Wool Newborn Gift",
    "Pressed Flower Resin Bookmark Handmade",
]


def seasonality_profile(peak_month: int, amplitude: float) -> dict:
    """12 monthly demand multipliers from a wrapped gaussian around the peak."""
    months = np.arange(1, 13)
    dist = np.minimum(np.abs(months - peak_month), 12 - np.abs(months - peak_month))
    profile = 1.0 + amplitude * 2.0 * np.exp(-(dist**2) / 3.0)
    return {"monthly": [round(float(v), 3) for v in profile]}


def build_taxonomy() -> list[dict]:
    rows = []
    for tid, name, cat, mat, diff, m_min, m_max, pers, peak, label, amp in TAXONOMY:
        rows.append(
            {
                "id": tid,
                "name": name,
                "category": cat,
                "material": mat,
                "difficulty": diff,
                "margin_min": m_min,
                "margin_max": m_max,
                "personalization_friendly": pers,
                "seasonality": {
                    **seasonality_profile(peak, amp),
                    "peak_month": peak,
                    "peak_label": label,
                },
            }
        )
    return rows


def template_aliases(row: dict) -> list[str]:
    """Deterministic fallback aliases; always mixed in for guaranteed recall."""
    name, mat = row["name"], row["material"]
    noun = name.split()[-1]
    return [
        name,
        f"Personalized {name}",
        f"Custom {name}",
        f"{mat.title()} {noun}",
        f"Custom {mat} {noun.lower()}",
        f"Personalized {mat} {noun.lower()} gift",
    ]


def llm_aliases(taxonomy: list[dict]) -> dict[str, list[str]]:
    """One-time LLM pass: 15-20 seller-style aliases per taxonomy node.
    Falls back to templates when the API is unreachable."""
    from pydantic import BaseModel

    from app.llm import get_llm

    class TypeAliases(BaseModel):
        product_type_id: str
        aliases: list[str]

    class AliasBatch(BaseModel):
        items: list[TypeAliases]

    llm = get_llm().with_structured_output(AliasBatch, method="function_calling")
    result: dict[str, list[str]] = {}
    batch_size = 5
    for i in range(0, len(taxonomy), batch_size):
        batch = taxonomy[i : i + batch_size]
        spec = "\n".join(
            f"- {r['id']}: {r['name']} ({r['material']}, {r['category']})" for r in batch
        )
        prompt = (
            "You generate realistic Etsy/Amazon seller listing names for print-on-demand "
            "products. For EACH product type below, write 15 diverse alias phrases the way "
            "real sellers title them: mix recipients (grandpa, mom, best friend), occasions "
            "(christmas, memorial, wedding), personalization words (custom, engraved, photo, "
            "name), and material/form words. Keep each alias 3-9 words, no quotes.\n\n"
            f"Product types:\n{spec}"
        )
        try:
            batch_result: AliasBatch = llm.invoke(prompt)
            for item in batch_result.items:
                if item.product_type_id in {r["id"] for r in batch}:
                    result[item.product_type_id] = item.aliases[:20]
            print(f"  aliases batch {i // batch_size + 1}: ok")
        except Exception as exc:
            print(f"  aliases batch {i // batch_size + 1} failed ({exc}); using templates")
    return result


def gen_listings(taxonomy: list[dict], aliases: dict[str, list[str]], rng: np.random.Generator):
    """Seeded synthetic listings shaped like Alura (Etsy) and Helium10 (Amazon)
    exports. Popularity is lognormal per type so percentiles spread."""
    current_month = 8  # generation reference month (Aug)
    popularity = {r["id"]: float(rng.lognormal(0.0, 0.8)) for r in taxonomy}
    # Force a few strong stories for the demo: a rising star, a saturated type.
    popularity["acrylic-ornament"] *= 3.0
    popularity["tshirt"] *= 4.0  # saturated: tons of listings, low margin story
    popularity["garden-stone"] *= 1.8

    etsy_rows, amazon_rows = [], []
    for row in taxonomy:
        pt = row["id"]
        pool = aliases.get(pt, []) or template_aliases(row)
        season = row["seasonality"]["monthly"][current_month - 1]
        pop = popularity[pt] * season
        n_etsy = max(6, int(rng.poisson(30 * popularity[pt])))
        n_amazon = max(4, int(rng.poisson(18 * popularity[pt])))
        base_price = {"acrylic": 22, "wood": 28, "metal": 30, "ceramic": 20, "glass": 24,
                      "canvas": 35, "fabric": 32, "stone": 38, "leather": 26, "paper": 18}[row["material"]]

        for _ in range(n_etsy):
            alias = pool[int(rng.integers(len(pool)))]
            title = (
                JUNK_PREFIX[int(rng.integers(len(JUNK_PREFIX)))]
                + alias
                + (f" For {RECIPIENTS[int(rng.integers(len(RECIPIENTS)))]}" if rng.random() < 0.5 else "")
                + (f" {OCCASIONS[int(rng.integers(len(OCCASIONS)))]} Gift" if rng.random() < 0.4 else "")
                + JUNK_SUFFIX[int(rng.integers(len(JUNK_SUFFIX)))]
            ).strip()
            sales = int(rng.lognormal(np.log(max(pop * 40, 1)), 0.9))
            etsy_rows.append({
                "Listing Title": title,
                "Price": round(float(rng.normal(base_price, base_price * 0.2)), 2),
                "Total Sales": sales * 6,
                "Est. Mo. Sales": sales,
                "Favorites": int(sales * rng.uniform(2, 8)),
                "Shop Name": f"Shop{int(rng.integers(100, 999))}Studio",
                "Tags": ",".join(rng.choice(
                    [row["material"], row["category"].lower(), "personalized gift", "custom",
                     "gift for her", "gift for him", "keepsake", "handmade"], size=4, replace=False)),
                "URL": f"https://www.etsy.com/listing/{int(rng.integers(10**8, 10**9))}",
                "_pt": pt,
            })
        for _ in range(n_amazon):
            alias = pool[int(rng.integers(len(pool)))]
            title = alias + (" - " + OCCASIONS[int(rng.integers(len(OCCASIONS)))] + " Gift" if rng.random() < 0.5 else "")
            sales = int(rng.lognormal(np.log(max(pop * 55, 1)), 1.0))
            price = round(float(rng.normal(base_price * 0.9, base_price * 0.15)), 2)
            amazon_rows.append({
                "Title": title,
                "Price": price,
                "Sales": sales,
                "Revenue": round(sales * price, 2),
                "Review Count": int(sales * rng.uniform(0.05, 0.25)),
                "Seller": f"Brand{int(rng.integers(100, 999))}",
                "URL": f"https://www.amazon.com/dp/B0{int(rng.integers(10**7, 10**8))}",
                "_pt": pt,
            })

    # Out-of-catalog noise (~10% of Etsy volume)
    for title in OUT_OF_CATALOG_TITLES:
        for _ in range(int(rng.integers(8, 20))):
            sales = int(rng.lognormal(3.0, 0.8))
            etsy_rows.append({
                "Listing Title": title + (JUNK_SUFFIX[int(rng.integers(len(JUNK_SUFFIX)))]),
                "Price": round(float(rng.uniform(10, 60)), 2),
                "Total Sales": sales * 6,
                "Est. Mo. Sales": sales,
                "Favorites": int(sales * rng.uniform(2, 8)),
                "Shop Name": f"Indie{int(rng.integers(100, 999))}Crafts",
                "Tags": "handmade,gift",
                "URL": f"https://www.etsy.com/listing/{int(rng.integers(10**8, 10**9))}",
                "_pt": "",
            })
    return etsy_rows, amazon_rows, popularity


def gen_keywords(taxonomy: list[dict], popularity: dict, rng: np.random.Generator) -> list[dict]:
    rows = []
    for row in taxonomy:
        pop = popularity[row["id"]]
        noun = row["name"].split()[-1].lower()
        seeds = [
            f"personalized {noun}",
            f"custom {row['material']} {noun}",
            f"{row['name'].lower()}",
            f"{noun} gift",
            f"engraved {noun}",
        ]
        rows.extend({
                "keyword": kw,
                "source": rng.choice(["alura_etsy", "helium10_amazon"]),
                "volume": int(rng.lognormal(np.log(max(pop * 2500, 50)), 0.7)),
                "competition": round(float(np.clip(rng.normal(0.35 + 0.1 * pop, 0.15), 0.05, 0.98)), 3),
                "cpc": round(float(rng.uniform(0.2, 1.8)), 2),
                "trend_30d": round(float(rng.normal(8 if pop > 1 else -2, 15)), 1),
                "product_type_id": row["id"],
            } for kw in seeds)
    return rows


def gen_trends(taxonomy: list[dict], popularity: dict, rng: np.random.Generator) -> list[dict]:
    """104 weekly Google-Trends-shaped points per product type."""
    from datetime import date, timedelta

    end = date(2026, 8, 17)  # most recent Monday before generation date
    growth = {r["id"]: float(rng.normal(0.0, 0.25)) for r in taxonomy}
    growth["acrylic-ornament"] = 0.45  # the demo "rising star"
    growth["garden-stone"] = 0.30
    growth["tshirt"] = -0.15  # saturated/declining story

    rows = []
    for row in taxonomy:
        monthly = row["seasonality"]["monthly"]
        g = growth[row["id"]]
        base = 30 + 40 * min(popularity[row["id"]], 2.0) / 2.0
        for w in range(104):
            d = end - timedelta(weeks=103 - w)
            season = monthly[d.month - 1]
            value = base * season * (1 + g * (w / 104)) + rng.normal(0, 3)
            rows.append({
                "date": d.isoformat(),
                "entity": row["id"],
                "entity_type": "product_type",
                "value": round(max(float(value), 0.0), 1),
            })
    return rows


def gen_eval_set(taxonomy: list[dict], aliases: dict[str, list[str]], rng: np.random.Generator) -> list[dict]:
    """~40 labeled titles: held-out aliases with junk affixes + OOC hard cases."""
    cases = []
    picked = rng.choice(len(taxonomy), size=30, replace=True)
    for idx in picked:
        row = taxonomy[int(idx)]
        pool = aliases.get(row["id"], []) or template_aliases(row)
        alias = pool[int(rng.integers(len(pool)))]
        title = (
            JUNK_PREFIX[int(rng.integers(len(JUNK_PREFIX)))]
            + alias
            + f" For {RECIPIENTS[int(rng.integers(len(RECIPIENTS)))]}"
            + JUNK_SUFFIX[int(rng.integers(len(JUNK_SUFFIX)))]
        ).strip()
        cases.append({"title": title, "expected": row["id"]})
    cases.extend({"title": title, "expected": None} for title in OUT_OF_CATALOG_TITLES)
    return cases


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    rng = np.random.default_rng(RNG_SEED)

    taxonomy = build_taxonomy()
    (DATA_DIR / "taxonomy.json").write_text(json.dumps(taxonomy, indent=2))
    print(f"taxonomy.json: {len(taxonomy)} product types")

    print("generating aliases via LLM (once)...")
    aliases = llm_aliases(taxonomy)
    # Always blend deterministic templates in for guaranteed recall.
    for row in taxonomy:
        merged = list(dict.fromkeys(template_aliases(row) + aliases.get(row["id"], [])))
        aliases[row["id"]] = merged
    (DATA_DIR / "aliases.json").write_text(json.dumps(aliases, indent=2))
    print(f"aliases.json: {sum(len(v) for v in aliases.values())} aliases")

    etsy, amazon, popularity = gen_listings(taxonomy, aliases, rng)
    for fname, rows in [("listings_etsy.csv", etsy), ("listings_amazon.csv", amazon)]:
        with open(DATA_DIR / fname, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"{fname}: {len(rows)} rows")

    keywords = gen_keywords(taxonomy, popularity, rng)
    with open(DATA_DIR / "keywords.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(keywords[0].keys()))
        writer.writeheader()
        writer.writerows(keywords)
    print(f"keywords.csv: {len(keywords)} rows")

    trends = gen_trends(taxonomy, popularity, rng)
    with open(DATA_DIR / "trends.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(trends[0].keys()))
        writer.writeheader()
        writer.writerows(trends)
    print(f"trends.csv: {len(trends)} rows")

    eval_set = gen_eval_set(taxonomy, aliases, rng)
    with open(DATA_DIR / "eval_listings.jsonl", "w") as f:
        f.writelines(json.dumps(case) + "\n" for case in eval_set)
    print(f"eval_listings.jsonl: {len(eval_set)} cases")


if __name__ == "__main__":
    main()
