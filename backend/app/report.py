"""Auto Research Report: fixed Markdown template filled from DB facts.

The LLM only writes short connective text (executive summary); every number
comes from the scores/listings tables. Degrades to pure template when the
LLM is unavailable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Listing, ProductType, Score
from app.scoring import weeks_to_peak

TOP_LISTINGS_FOR_INSIGHTS = 15


def _design_snapshot(db: Session, product_type_id: str) -> dict:
    listings = list(
        db.scalars(
            select(Listing)
            .where(Listing.product_type_id == product_type_id)
            .order_by(Listing.est_sales.desc())
            .limit(TOP_LISTINGS_FOR_INSIGHTS)
        )
    )
    tag_counts: dict[str, int] = {}
    for listing in listings:
        for tag in listing.tags or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda kv: -kv[1])[:6]
    prices = [listing.price for listing in listings]
    return {
        "top_tags": [t for t, _ in top_tags],
        "price_min": min(prices) if prices else 0,
        "price_max": max(prices) if prices else 0,
        "price_avg": sum(prices) / len(prices) if prices else 0,
        "example_titles": [listing.title for listing in listings[:3]],
    }


def _llm_summary(context: str) -> str | None:
    try:
        from app.llm import get_langfuse_handler, get_llm

        handler = get_langfuse_handler()
        config = {"callbacks": [handler]} if handler else {}
        result = get_llm().invoke(
            "Write a 3-sentence executive summary for a product research report. "
            "Use ONLY the facts provided; no invented numbers. Plain prose, no headers.\n\n"
            f"{context}",
            config=config,
        )
        return str(result.content)
    except Exception:  # noqa: BLE001 - template still ships without the LLM
        return None


def build_report(db: Session, product_type_id: str) -> str:
    t = db.get(ProductType, product_type_id)
    score = db.get(Score, product_type_id)
    if not t or not score:
        return f"# Report unavailable\n\nNo score computed for `{product_type_id}`."

    now = datetime.now(timezone.utc)
    season = t.seasonality or {}
    peak_month = int(season.get("peak_month", 12))
    peak_label = season.get("peak_label", "Holiday")
    wtp = weeks_to_peak(peak_month, now)
    design = _design_snapshot(db, product_type_id)
    dims = score.dims

    decision_label = {
        "recommend": "✅ RECOMMEND",
        "conditional": "🟡 CONDITIONAL",
        "not_recommend": "❌ NOT RECOMMENDED",
    }[score.decision]

    summary_context = (
        f"Product: {t.name} ({t.material}, {t.category}). "
        f"Opportunity score {score.total}/100, manufacturing fit {score.fit}/100, "
        f"decision {score.decision}. Demand {dims['demand']['value']}, growth "
        f"{dims['growth']['value']}, competition {dims['competition']['value']}. "
        f"Peak demand: {peak_label}, {wtp} weeks away."
    )
    llm_text = _llm_summary(summary_context)

    dim_rows = "\n".join(
        f"| {k.title()} | {v['value']}/100 | {v['explanation']} |" for k, v in dims.items()
    )
    evidence_rows = "\n".join(
        f"| {ev['metric']} | {ev['value']} | {ev['source']} | {ev['fetched_at'][:16]} |"
        for v in dims.values()
        for ev in v["evidence"]
    )

    return f"""# Product Research Report: {t.name}

**Decision: {decision_label}** · Opportunity Score **{score.total}/100** · Manufacturing Fit **{score.fit}/100**
_Generated {now.strftime('%Y-%m-%d %H:%M UTC')} · sources: Etsy (Alura), Amazon (Helium10), Google Trends_

## Executive summary

{llm_text or summary_context}

## Product specification

| Field | Value |
|---|---|
| Product Type | {t.name} (`{t.id}`) |
| Category | {t.category} |
| Material | {t.material} |
| Production difficulty | {t.difficulty}/5 |
| Margin band | {t.margin_min:.0%} – {t.margin_max:.0%} |
| Personalization-friendly | {"yes" if t.personalization_friendly else "no"} |

## Score breakdown

| Dimension | Score | Why |
|---|---|---|
{dim_rows}

**Fit gate:** fit {score.fit}/100 → {score.decision.replace("_", " ")} (fit <50 blocks any recommendation regardless of market score).

## Launch timing

- Peak demand: **{peak_label}** (month {peak_month})
- Weeks to peak: **{wtp}**
- {dims['seasonality']['explanation']}

## Design direction

- Top listing tags: {", ".join(design['top_tags']) or "n/a"}
- Price range on market: ${design['price_min']:.0f}–${design['price_max']:.0f} (avg ${design['price_avg']:.0f})
- Best-selling title patterns:
{chr(10).join(f"  - {title}" for title in design['example_titles']) or "  - n/a"}

## Suggested listing price

Target **${design['price_avg'] * 1.05:.0f}** (slightly above market average — justified by personalization premium) with margin inside the {t.margin_min:.0%}–{t.margin_max:.0%} band.

## Evidence

| Metric | Value | Source | Fetched |
|---|---|---|---|
{evidence_rows}

## Risks

- Competition can saturate fast; re-check listing counts 2 weeks before launch.
- Seasonality window: production + listing must complete within {max(wtp - 2, 0)} weeks.
- Scores are percentile-relative to the current catalog cohort — recompute after data refresh.
"""
