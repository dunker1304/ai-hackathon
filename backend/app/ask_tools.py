"""Deterministic tool registry for the Ask layer.

Every tool is a plain function (db, **kwargs) -> JSON-safe dict that queries
the DB — the LLM never computes numbers, it only picks tools and narrates
results. Each result carries a "sources" list for citation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import normalize as normalize_mod
from app import report as report_mod
from app.models import Listing, ProductType, Score
from app.scoring import weeks_to_peak


# --- Argument schemas (become OpenAI tool schemas via bind_tools) ---


class RankOpportunitiesArgs(BaseModel):
    """Rank the best product opportunities, optionally filtered by category, material, or minimum manufacturing fit."""

    category: str | None = Field(None, description="Filter by taxonomy category, e.g. 'Ornaments'")
    material: str | None = Field(None, description="Filter by material: acrylic, wood, metal, ceramic, glass, canvas, fabric, stone, leather, paper")
    min_fit: int | None = Field(None, description="Minimum manufacturing fit 0-100")
    limit: int = Field(10, description="Max results")


class ExplainScoreArgs(BaseModel):
    """Full score breakdown for one product type: every dimension, raw values, evidence, and the fit gate."""

    product_type_id: str = Field(..., description="Taxonomy id, e.g. 'acrylic-ornament'")


class NormalizeListingArgs(BaseModel):
    """Map a marketplace listing title to the Printway catalog taxonomy (or report out-of-catalog)."""

    title: str = Field(..., description="The raw listing title to normalize")


class CompareNichesArgs(BaseModel):
    """Compare 2-4 product types dimension by dimension."""

    product_type_ids: list[str] = Field(..., description="2-4 taxonomy ids to compare")


class SeasonalityWindowArgs(BaseModel):
    """Launch-window verdict for a product type: weeks to demand peak and whether launching now is timely."""

    product_type_id: str


class DesignInsightsArgs(BaseModel):
    """Design direction for a product type: top tags, price band, and best-selling title patterns."""

    product_type_id: str


class GenerateReportArgs(BaseModel):
    """Generate the full markdown Product Research Report for a product type."""

    product_type_id: str


# --- Tool implementations ---


def _score_row(db: Session, s: Score) -> dict:
    t = db.get(ProductType, s.product_type_id)
    return {
        "product_type_id": s.product_type_id,
        "name": t.name if t else s.product_type_id,
        "category": t.category if t else "?",
        "material": t.material if t else "?",
        "total": s.total,
        "fit": s.fit,
        "decision": s.decision,
        "dims": {k: v["value"] for k, v in s.dims.items()},
    }


def rank_opportunities(db: Session, category: str | None = None, material: str | None = None,
                       min_fit: int | None = None, limit: int = 10) -> dict:
    stmt = select(Score).join(ProductType, ProductType.id == Score.product_type_id)
    if category:
        stmt = stmt.where(ProductType.category.ilike(f"%{category}%"))
    if material:
        stmt = stmt.where(ProductType.material.ilike(material.strip().lower()))
    if min_fit is not None:
        stmt = stmt.where(Score.fit >= min_fit)
    stmt = stmt.order_by(Score.total.desc()).limit(min(limit, 20))
    rows = [_score_row(db, s) for s in db.scalars(stmt)]
    return {
        "items": rows,
        "sources": ["etsy (alura export)", "amazon (helium10 export)", "google_trends"],
        "note": "totals use default weights; fit is a manufacturing gate, not a dimension",
    }


def explain_score(db: Session, product_type_id: str) -> dict:
    s = db.get(Score, product_type_id)
    if not s:
        return {"error": f"no score for '{product_type_id}'", "sources": []}
    t = db.get(ProductType, product_type_id)
    return {
        "product_type_id": product_type_id,
        "name": t.name if t else product_type_id,
        "total": s.total,
        "fit": s.fit,
        "decision": s.decision,
        "dims": s.dims,
        "computed_at": str(s.computed_at),
        "sources": sorted({ev["source"] for v in s.dims.values() for ev in v["evidence"]}),
    }


def normalize_listing(db: Session, title: str) -> dict:
    result = normalize_mod.normalize_listing(db, title)
    pt = result["product_type"]
    return {
        "status": result["status"],
        "product_type": (
            {"id": pt.id, "name": pt.name, "category": pt.category, "material": pt.material, "fit": pt.fit}
            if pt
            else None
        ),
        "confidence": result["confidence"],
        "reasoning": result["reasoning"],
        "evidence_span": result["evidence_span"],
        "candidates": result["candidates"][:5],
        "sources": ["printway taxonomy + alias index"],
    }


def compare_niches(db: Session, product_type_ids: list[str]) -> dict:
    rows = []
    for pt_id in product_type_ids[:4]:
        s = db.get(Score, pt_id)
        if s:
            rows.append(_score_row(db, s))
    if not rows:
        return {"error": "none of the given ids have scores", "sources": []}
    best = max(rows, key=lambda r: r["total"])
    return {
        "items": rows,
        "verdict": f"{best['name']} has the highest opportunity score ({best['total']}) at default weights",
        "sources": ["etsy (alura export)", "amazon (helium10 export)", "google_trends"],
    }


def seasonality_window(db: Session, product_type_id: str) -> dict:
    t = db.get(ProductType, product_type_id)
    s = db.get(Score, product_type_id)
    if not t:
        return {"error": f"unknown product type '{product_type_id}'", "sources": []}
    season = t.seasonality or {}
    wtp = weeks_to_peak(int(season.get("peak_month", 12)), datetime.now(timezone.utc))
    return {
        "product_type_id": product_type_id,
        "peak_label": season.get("peak_label"),
        "peak_month": season.get("peak_month"),
        "weeks_to_peak": wtp,
        "seasonality_score": s.dims["seasonality"]["value"] if s else None,
        "explanation": s.dims["seasonality"]["explanation"] if s else None,
        "sources": ["google_trends (5y seasonal profile)"],
    }


def design_insights(db: Session, product_type_id: str) -> dict:
    snapshot = report_mod._design_snapshot(db, product_type_id)
    listings_count = db.scalar(
        select(Listing.id).where(Listing.product_type_id == product_type_id).limit(1)
    )
    if listings_count is None:
        return {"error": f"no listings tracked for '{product_type_id}'", "sources": []}
    return {**snapshot, "sources": ["etsy (alura export)", "amazon (helium10 export)"]}


def generate_report(db: Session, product_type_id: str) -> dict:
    return {
        "markdown": report_mod.build_report(db, product_type_id),
        "sources": ["etsy", "amazon", "google_trends", "printway catalog"],
    }


# --- Registry ---

TOOLS: dict[str, tuple[type[BaseModel], Callable]] = {
    "rank_opportunities": (RankOpportunitiesArgs, rank_opportunities),
    "explain_score": (ExplainScoreArgs, explain_score),
    "normalize_listing": (NormalizeListingArgs, normalize_listing),
    "compare_niches": (CompareNichesArgs, compare_niches),
    "seasonality_window": (SeasonalityWindowArgs, seasonality_window),
    "design_insights": (DesignInsightsArgs, design_insights),
    "generate_report": (GenerateReportArgs, generate_report),
}


def tool_schemas() -> list[type[BaseModel]]:
    """Pydantic classes for llm.bind_tools(); class name = tool name."""
    schemas = []
    for name, (args_model, _) in TOOLS.items():
        # bind_tools uses the class name as the tool name; rename dynamically.
        schemas.append(type(name, (args_model,), {"__doc__": args_model.__doc__}))
    return schemas


def run_tool(db: Session, name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool '{name}'", "sources": []}
    args_model, fn = TOOLS[name]
    try:
        parsed = args_model(**args)
        return fn(db, **parsed.model_dump())
    except Exception as exc:  # noqa: BLE001 - tool errors go back to the LLM
        return {"error": str(exc)[:200], "sources": []}
