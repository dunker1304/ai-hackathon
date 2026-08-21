"""Product Opportunity Hub endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ask import run_ask
from app.db import get_db
from app.models import Listing, ProductType, Score
from app.normalize import normalize_listing
from app.report import build_report
from app.schemas import (
    AskRequest,
    FreshnessResponse,
    FreshnessSource,
    NormalizeRequest,
    NormalizeResponse,
    OpportunitiesResponse,
    OpportunityOut,
    ProductTypeOut,
    ReportResponse,
)
from app.scoring import DEFAULT_WEIGHTS

router = APIRouter(tags=["hub"])


@router.get("/taxonomy", response_model=list[ProductTypeOut])
def get_taxonomy(db: Session = Depends(get_db)) -> list[ProductTypeOut]:
    return [ProductTypeOut.model_validate(t) for t in db.scalars(select(ProductType))]


def _opportunity_out(s: Score, t: ProductType) -> OpportunityOut:
    return OpportunityOut(
        product_type_id=s.product_type_id,
        name=t.name,
        category=t.category,
        material=t.material,
        total=s.total,
        fit=s.fit,
        decision=s.decision,
        dims=s.dims,
        computed_at=str(s.computed_at),
    )


@router.get("/opportunities", response_model=OpportunitiesResponse)
def list_opportunities(
    category: str | None = None,
    material: str | None = None,
    db: Session = Depends(get_db),
) -> OpportunitiesResponse:
    stmt = (
        select(Score, ProductType)
        .join(ProductType, ProductType.id == Score.product_type_id)
        .order_by(Score.total.desc())
    )
    if category:
        stmt = stmt.where(ProductType.category == category)
    if material:
        stmt = stmt.where(ProductType.material == material)
    items = [_opportunity_out(s, t) for s, t in db.execute(stmt)]
    return OpportunitiesResponse(items=items, default_weights=DEFAULT_WEIGHTS)


@router.get("/compare", response_model=OpportunitiesResponse)
def compare(
    ids: str = Query(..., description="Comma-separated product type ids (2-4)"),
    db: Session = Depends(get_db),
) -> OpportunitiesResponse:
    wanted = [i.strip() for i in ids.split(",") if i.strip()][:4]
    if len(wanted) < 2:
        raise HTTPException(status_code=422, detail="Provide at least 2 ids")
    stmt = (
        select(Score, ProductType)
        .join(ProductType, ProductType.id == Score.product_type_id)
        .where(Score.product_type_id.in_(wanted))
    )
    items = [_opportunity_out(s, t) for s, t in db.execute(stmt)]
    return OpportunitiesResponse(items=items, default_weights=DEFAULT_WEIGHTS)


@router.post("/normalize", response_model=NormalizeResponse)
def normalize(payload: NormalizeRequest, db: Session = Depends(get_db)) -> NormalizeResponse:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    result = normalize_listing(db, title)
    pt = result["product_type"]
    return NormalizeResponse(
        status=result["status"],
        product_type=ProductTypeOut.model_validate(pt) if pt else None,
        confidence=result["confidence"],
        reasoning=result["reasoning"],
        evidence_span=result["evidence_span"],
        candidates=result["candidates"],
        signature=result["signature"],
    )


@router.post("/ask")
def ask(payload: AskRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be empty")
    return StreamingResponse(
        run_ask(db, question, payload.history),
        media_type="application/x-ndjson",
    )


@router.get("/report/{product_type_id}", response_model=ReportResponse)
def report(product_type_id: str, db: Session = Depends(get_db)) -> ReportResponse:
    if not db.get(ProductType, product_type_id):
        raise HTTPException(status_code=404, detail=f"unknown product type '{product_type_id}'")
    return ReportResponse(
        product_type_id=product_type_id,
        markdown=build_report(db, product_type_id),
    )


@router.get("/meta/freshness", response_model=FreshnessResponse)
def freshness(db: Session = Depends(get_db)) -> FreshnessResponse:
    rows = db.execute(
        select(Listing.source, func.max(Listing.fetched_at), func.count()).group_by(Listing.source)
    ).all()
    sources = [
        FreshnessSource(source=src, fetched_at=str(ts), count=count) for src, ts, count in rows
    ]
    trends_ts = db.scalar(select(func.max(Score.computed_at)))
    if sources:
        sources.append(
            FreshnessSource(
                source="google_trends",
                fetched_at=str(trends_ts) if trends_ts else "",
                count=int(db.scalar(select(func.count()).select_from(Score)) or 0),
            )
        )
    return FreshnessResponse(
        sources=sources,
        scores_computed_at=str(trends_ts) if trends_ts else None,
    )
