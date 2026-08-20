from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.rag import ingest_document
from app.schemas import IngestRequest, IngestResponse
from database.db import get_db

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("")
async def ingest(payload: IngestRequest, db: Annotated[Session, Depends(get_db)]) -> IngestResponse:
    doc_id, count = await ingest_document(db, payload.title, payload.text, payload.source)
    return IngestResponse(document_id=doc_id, chunks_created=count)
