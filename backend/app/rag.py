"""
Minimal RAG pipeline: chunk -> embed -> store (ingest), and
embed query -> pgvector cosine search -> top-k chunks (retrieve).
Swap the chunker or add a reranker here without touching the routers.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import get_embeddings
from app.models import Chunk, Document


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Simple fixed-size chunker with overlap. Good enough for a hackathon;
    swap for semantic chunking (e.g. via LangChain text splitters) if quality matters."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]


def ingest_document(db: Session, title: str, text: str, source: str | None = None) -> tuple[int, int]:
    doc = Document(title=title, source=source)
    db.add(doc)
    db.flush()  # get doc.id without committing yet

    pieces = chunk_text(text)
    embedder = get_embeddings()
    vectors = embedder.embed_documents(pieces)

    for content, vector in zip(pieces, vectors):
        db.add(Chunk(document_id=doc.id, content=content, embedding=vector))

    db.commit()
    return doc.id, len(pieces)


def retrieve(db: Session, question: str, top_k: int = 5) -> list[Chunk]:
    embedder = get_embeddings()
    query_vector = embedder.embed_query(question)

    stmt = (
        select(Chunk)
        .order_by(Chunk.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )
    return list(db.scalars(stmt))
