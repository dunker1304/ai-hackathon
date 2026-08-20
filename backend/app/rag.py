"""
Minimal RAG pipeline: chunk -> embed -> store (ingest), and
embed query -> pgvector cosine search -> top-k chunks (retrieve).
Swap the chunker or add a reranker here without touching the routers.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.model import Chunk, Document
from app.llm import get_embeddings


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Simple fixed-size chunker with overlap. Good enough for a hackathon; swap for
    semantic chunking (e.g. via LangChain text splitters) if quality matters."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]


async def ingest_document(db: AsyncSession, title: str, text: str, source: str | None = None) -> tuple[int, int]:
    doc = Document(title=title, source=source)
    db.add(doc)
    await db.flush()  # get doc.id without committing yet

    pieces = chunk_text(text)
    embedder = get_embeddings()
    vectors = embedder.embed_documents(pieces)

    for content, vector in zip(pieces, vectors):
        db.add(Chunk(document_id=doc.id, content=content, embedding=vector))

    await db.commit()
    return doc.id, len(pieces)


async def retrieve(db: AsyncSession, question: str, top_k: int = 5) -> list[Chunk]:
    embedder = get_embeddings()
    query_vector = embedder.embed_query(question)

    stmt = select(Chunk).order_by(Chunk.embedding.cosine_distance(query_vector)).limit(top_k)
    result = await db.scalars(stmt)
    return list(result.all())
