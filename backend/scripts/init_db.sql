-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Source documents (optional grouping/metadata)
CREATE TABLE IF NOT EXISTS documents (
    id          bigserial PRIMARY KEY,
    title       text NOT NULL,
    source      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Chunks + embeddings
-- 1536 dims matches OpenAI text-embedding-3-small.
-- Change this AND the embedding model in app/rag.py together.
CREATE TABLE IF NOT EXISTS chunks (
    id           bigserial PRIMARY KEY,
    document_id  bigint REFERENCES documents(id) ON DELETE CASCADE,
    content      text NOT NULL,
    embedding    vector(1536) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- HNSW index: can be built before data exists, best recall/latency tradeoff in 2026.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
