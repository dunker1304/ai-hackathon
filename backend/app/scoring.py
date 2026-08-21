"""Opportunity Score computation.

Six explainable dimensions, percentile-normalized within category cohorts,
plus a manufacturing-fit GATE (not an additive dimension). Everything is
computed in pandas at seed time and stored in scores.dims as:

    {"demand": {"value": 82.0, "raw": 1240.0,
                "explanation": "...",
                "evidence": [{"metric": "est_sales_sum", "value": 1240,
                              "source": "etsy", "fetched_at": "..."}]},
     ...}

That single JSON powers the weight sliders (client recompute), explain_score
tool, evidence popovers, and the freshness badges.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Keyword, Listing, ProductType, Score, TrendPoint

DEFAULT_WEIGHTS: dict[str, float] = {
    "demand": 0.22,
    "competition": 0.20,
    "growth": 0.20,
    "seasonality": 0.12,
    "personalization": 0.13,
    "revenue": 0.13,
}

MIN_COHORT_SIZE = 5
GROWTH_WINDOW_WEEKS = 12
PERSONALIZATION_TITLE_WORDS = ("personalized", "custom", "engraved", "monogram", "photo", "name")

# Launch-window bands: (min_weeks_to_peak, max_weeks_to_peak, score)
LAUNCH_WINDOW_BANDS = [
    (8, 16, 95.0),   # ideal: time to produce + list + gather reviews
    (6, 8, 75.0),    # tight but doable
    (0, 6, 50.0),    # late
    (16, 33, 65.0),  # early — acceptable, capital parked
]
PAST_PEAK_SCORE = 20.0


def weeks_to_peak(peak_month: int, today: datetime) -> int:
    """Whole weeks from today until the next occurrence of the peak month's start."""
    year = today.year if today.month < peak_month else today.year + 1
    if today.month == peak_month:
        return 0
    peak_date = datetime(year, peak_month, 1, tzinfo=timezone.utc)
    return max(int((peak_date - today).days // 7), 0)


def seasonality_score(peak_month: int, peak_label: str, today: datetime) -> tuple[float, str]:
    wtp = weeks_to_peak(peak_month, today)
    if wtp == 0:
        return PAST_PEAK_SCORE, (
            f"Peak demand ({peak_label}) is happening now — too late to launch for this cycle."
        )
    for lo, hi, score in LAUNCH_WINDOW_BANDS:
        if lo <= wtp < hi:
            note = {
                95.0: "ideal launch window (time for production + listing + reviews)",
                75.0: "tight but feasible launch window",
                50.0: "late — rushing production risks missing the peak",
                65.0: "early — you can launch now and build listing history",
            }[score]
            return score, f"Peak demand: {peak_label} (month {peak_month}). {wtp} weeks out — {note}."
    return 60.0, f"Peak demand: {peak_label} (month {peak_month}). {wtp} weeks out."


def _pct_rank(df: pd.DataFrame, col: str) -> pd.Series:
    """Percentile 0-100 within category cohort; global fallback for tiny cohorts."""
    global_rank = df[col].rank(pct=True) * 100
    cohort_rank = df.groupby("category")[col].rank(pct=True) * 100
    sizes = df.groupby("category")["category"].transform("size")
    return np.where(sizes >= MIN_COHORT_SIZE, cohort_rank, global_rank)


def _evidence(metric: str, value: float, source: str, fetched_at: str) -> dict:
    return {"metric": metric, "value": round(float(value), 2), "source": source, "fetched_at": fetched_at}


def compute_all(db: Session, now: datetime | None = None) -> int:
    """Recompute every product type's score. Returns number of rows written."""
    now = now or datetime.now(timezone.utc)

    types = list(db.scalars(select(ProductType)))
    listings = pd.read_sql(select(Listing).where(Listing.product_type_id.is_not(None)), db.get_bind())
    keywords = pd.read_sql(select(Keyword), db.get_bind())
    trends = pd.read_sql(select(TrendPoint).where(TrendPoint.entity_type == "product_type"), db.get_bind())

    freshness = {
        src: str(ts)
        for src, ts in listings.groupby("source")["fetched_at"].max().items()
    }

    rows = []
    for t in types:
        lst = listings[listings["product_type_id"] == t.id]
        kws = keywords[keywords["product_type_id"] == t.id]
        trd = trends[trends["entity"] == t.id].sort_values("date")

        est_sales = float(lst["est_sales"].sum())
        favorites = float(lst["favorites"].sum())
        demand_raw = est_sales + 0.3 * favorites

        n_listings = int(len(lst))
        kw_comp = float(kws["competition"].mean()) if len(kws) else 0.5
        competition_raw = -(n_listings * kw_comp)  # inverted: fewer competitors = higher

        if len(trd) >= GROWTH_WINDOW_WEEKS:
            recent = trd.tail(GROWTH_WINDOW_WEEKS)
            x = np.arange(len(recent))
            slope = float(np.polyfit(x, recent["value"].to_numpy(), 1)[0])
            mean_v = float(recent["value"].mean()) or 1.0
            growth_raw = slope / mean_v * 100  # % change per week, normalized
        else:
            growth_raw = 0.0

        revenue_raw = float((lst["est_sales"] * lst["price"]).sum())

        pers_share = (
            float(lst["title"].str.lower().str.contains("|".join(PERSONALIZATION_TITLE_WORDS)).mean())
            if len(lst)
            else 0.0
        )
        personalization_raw = (60.0 if t.personalization_friendly else 10.0) + 40.0 * pers_share

        season = t.seasonality or {}
        s_score, s_explanation = seasonality_score(
            int(season.get("peak_month", 12)), str(season.get("peak_label", "Holiday")), now
        )

        rows.append(
            {
                "product_type_id": t.id,
                "category": t.category,
                "fit": t.fit,
                "demand_raw": demand_raw,
                "competition_raw": competition_raw,
                "growth_raw": growth_raw,
                "revenue_raw": revenue_raw,
                "personalization": min(personalization_raw, 100.0),
                "seasonality": s_score,
                "seasonality_explanation": s_explanation,
                "est_sales": est_sales,
                "favorites": favorites,
                "n_listings": n_listings,
                "kw_competition": kw_comp,
                "growth_pct_wk": growth_raw,
                "pers_share": pers_share,
            }
        )

    df = pd.DataFrame(rows)
    df["demand"] = _pct_rank(df, "demand_raw")
    df["competition"] = _pct_rank(df, "competition_raw")
    df["growth"] = _pct_rank(df, "growth_raw")
    df["revenue"] = _pct_rank(df, "revenue_raw")

    etsy_ts = freshness.get("etsy", str(now))
    amazon_ts = freshness.get("amazon", str(now))

    written = 0
    for _, r in df.iterrows():
        dims = {
            "demand": {
                "value": round(float(r["demand"]), 1),
                "raw": round(float(r["demand_raw"]), 1),
                "explanation": (
                    f"{int(r['est_sales']):,} est. monthly sales + {int(r['favorites']):,} favorites "
                    f"→ top {100 - r['demand']:.0f}% of its category."
                ),
                "evidence": [
                    _evidence("est_sales_sum", r["est_sales"], "etsy+amazon", etsy_ts),
                    _evidence("favorites_sum", r["favorites"], "etsy", etsy_ts),
                ],
            },
            "competition": {
                "value": round(float(r["competition"]), 1),
                "raw": round(float(r["competition_raw"]), 2),
                "explanation": (
                    f"{int(r['n_listings'])} competing listings, keyword competition "
                    f"{r['kw_competition']:.2f} (inverted — fewer competitors scores higher)."
                ),
                "evidence": [
                    _evidence("listing_count", r["n_listings"], "etsy+amazon", amazon_ts),
                    _evidence("keyword_competition_avg", r["kw_competition"], "alura+helium10", etsy_ts),
                ],
            },
            "growth": {
                "value": round(float(r["growth"]), 1),
                "raw": round(float(r["growth_raw"]), 2),
                "explanation": (
                    f"Search interest trend {r['growth_pct_wk']:+.1f}%/week over the last "
                    f"{GROWTH_WINDOW_WEEKS} weeks."
                ),
                "evidence": [
                    _evidence("trend_slope_pct_per_week", r["growth_pct_wk"], "google_trends", str(now)),
                ],
            },
            "seasonality": {
                "value": round(float(r["seasonality"]), 1),
                "raw": round(float(r["seasonality"]), 1),
                "explanation": str(r["seasonality_explanation"]),
                "evidence": [
                    _evidence("weeks_to_peak_score", r["seasonality"], "google_trends (5y profile)", str(now)),
                ],
            },
            "personalization": {
                "value": round(float(r["personalization"]), 1),
                "raw": round(float(r["pers_share"]), 3),
                "explanation": (
                    f"{r['pers_share']:.0%} of listings advertise personalization; "
                    "catalog marks this type personalization-friendly."
                ),
                "evidence": [
                    _evidence("personalized_listing_share", r["pers_share"] * 100, "etsy", etsy_ts),
                ],
            },
            "revenue": {
                "value": round(float(r["revenue"]), 1),
                "raw": round(float(r["revenue_raw"]), 0),
                "explanation": (
                    f"~${r['revenue_raw']:,.0f}/month estimated revenue across tracked listings."
                ),
                "evidence": [
                    _evidence("est_monthly_revenue_usd", r["revenue_raw"], "etsy+amazon", amazon_ts),
                ],
            },
        }
        total = compute_total(dims, DEFAULT_WEIGHTS)
        fit = int(r["fit"])
        db.merge(
            Score(
                product_type_id=r["product_type_id"],
                computed_at=now,
                dims=dims,
                total=round(total, 1),
                fit=fit,
                decision=decide(total, fit),
            )
        )
        written += 1

    db.commit()
    return written


def compute_total(dims: dict, weights: dict[str, float]) -> float:
    wsum = sum(weights.values()) or 1.0
    return sum(weights[k] * dims[k]["value"] for k in weights) / wsum


def decide(total: float, fit: int) -> str:
    """Fit is a gate, not a dimension: strong market + weak manufacturing fit
    must NOT be recommended."""
    if fit < 50:
        return "not_recommend"
    if fit < 70:
        return "conditional"
    return "recommend" if total >= 70 else ("conditional" if total >= 50 else "not_recommend")


def compute_fit(difficulty: int, margin_min: float, margin_max: float) -> int:
    """Manufacturing fit 0-100 from catalog difficulty (1-5) and margin band."""
    difficulty_score = (5 - difficulty) / 4 * 100  # 1 → 100, 5 → 0
    margin_score = min(((margin_min + margin_max) / 2) / 0.6, 1.0) * 100  # 60% avg margin = perfect
    return int(round(0.5 * difficulty_score + 0.5 * margin_score))
