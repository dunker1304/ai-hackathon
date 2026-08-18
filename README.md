# Hackathon AI Boilerplate

RAG-ready full-stack starter: **FastAPI (uv) + LangChain + OpenRouter + Langfuse + pgvector + Nuxt 3**.

Clone this, add your API keys, and you have a working chat-over-your-documents app in under 10 minutes.

## Stack

| Layer | Tool |
|---|---|
| Frontend | Nuxt 3 |
| Backend | FastAPI, served via `uv` |
| LLM gateway | OpenRouter (one key, many models) |
| Orchestration | LangChain |
| Observability | Langfuse |
| Vector store | PostgreSQL + pgvector (HNSW index) |

## Quick start

### 1. Database

```bash
docker compose up -d db
```

This starts Postgres with the `pgvector` extension already installed, and runs `scripts/init_db.sql` to create the schema + HNSW index.

### 2. Backend

```bash
cd backend
cp .env.example .env      # fill in OPENROUTER_API_KEY, LANGFUSE_* keys
uv sync  # uv sync --group dev -> for sync dependencies in local dev
uv run uvicorn app.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

### 3. Frontend

```bash
cd frontend
cp .env.example .env      # set NUXT_PUBLIC_API_BASE if backend isn't on :8000
npm install
npm run dev
```

App at `http://localhost:3000`.

## What's wired up already

- **`POST /documents`** — ingest raw text: chunks it, embeds it, stores in `chunks` table.
- **`POST /chat`** — RAG endpoint: embeds the question, retrieves top-k chunks via pgvector cosine distance, streams an answer from the LLM through LangChain, traced in Langfuse.
- **Model routing** — swap models by changing one string in `.env` (`OPENROUTER_MODEL`), no code changes. Any OpenRouter-supported model works (Claude, GPT, Gemini, open-weights).
- **Tracing** — every LLM call and retrieval step shows up in your Langfuse dashboard automatically via the LangChain callback handler.
- **Frontend chat UI** — minimal streaming chat page in Nuxt 3, already wired to the backend.

## Extending during the hackathon

- Swap `text-embedding-3-small` for another embedding model in `app/rag.py` — just make sure the `vector()` column dimension in `scripts/init_db.sql` matches.
- Add a reranker (Cohere Rerank / BGE) between retrieval and generation in `app/rag.py` if answer quality is the bottleneck.
- Add more routers under `app/routers/` following the pattern in `chat.py`.
- If you don't need RAG at all, just call `app/llm.py`'s `get_llm()` directly — the gateway + tracing still works.
