# Product Opportunity Hub

AI Product Research Copilot for POD (print-on-demand) R&D teams — built for the Printway hackathon challenge (PW1).

Aggregates marketplace signals (Etsy via Alura exports, Amazon via Helium10 exports, Google Trends) into a single explainable **Opportunity Score** with an actionable recommendation: what to make, which material, when to launch.

## Stack

| Layer | Tool |
|---|---|
| Frontend | Nuxt 4 + Nuxt UI + Tailwind 4 |
| Backend | FastAPI (uv), SQLAlchemy 2, Alembic |
| LLM gateway | OpenRouter (one key, many models) |
| Orchestration | LangChain (tool-calling Ask layer) |
| Observability | Langfuse |
| Vector store | PostgreSQL + pgvector (HNSW alias index) |

## Core capabilities

1. **Trend Aggregation** — signals from ≥2 independent sources (Etsy + Amazon exports, Google-Trends-shaped weekly series), every number carries `source` + `fetched_at`.
2. **Product Type Normalization** — 4-stage pipeline (LLM signature extraction → multi-vector alias retrieval → constrained LLM choice → keyword rule override) maps any listing title to the Printway taxonomy. Honest `out_of_catalog` below the confidence threshold. Eval: **top-1 93% · top-3 100% · OOC 100%** (`scripts/eval_normalization.py`).
3. **Opportunity Score** — 6 explainable dimensions (Demand 22%, Competition 20%, Growth 20%, Seasonality 12%, Personalization 13%, Revenue 13%), percentile-normalized within category cohorts. **Manufacturing Fit is a gate, not a dimension**: fit < 50 blocks recommendation regardless of market score. Weight sliders recompute totals client-side, instantly.
4. **Ask copilot** — no text RAG: the LLM routes intent to 7 deterministic SQL/pandas tools (`rank_opportunities`, `explain_score`, `normalize_listing`, `compare_niches`, `seasonality_window`, `design_insights`, `generate_report`) and narrates the results. Every answer ends with a **→ Recommendation** block.
5. **Auto Research Report** — fixed Markdown template (LLM writes connective text only), downloadable per product type.

## Quick start

### 1. Database

```bash
docker compose up -d db
cd backend
uv sync
uv run alembic upgrade head        # schema migrations (Alembic)
```

### 2. Seed data

```bash
uv run python scripts/seed.py      # loads backend/data/* into Postgres + computes scores
```

Mock data is committed in `backend/data/` (generated once by `scripts/generate_mock_data.py`).
**Swap in real BTC data**: drop real Alura/Helium10 exports with the same column names into
`backend/data/` and re-run `seed.py` — the `ADAPTERS` dict in `scripts/seed.py` is the only seam.

> Alias embeddings need a valid `OPENAI_API_KEY`. Without one, seeding still works and
> normalization falls back to lexical matching (already at 93% top-1 on the eval set).
> Fix the key and re-run `seed.py` to enable vector retrieval.

### 3. Backend

```bash
cd backend
cp .env.example .env               # OPENROUTER_API_KEY (needs credits), OPENAI_API_KEY, LANGFUSE_*
uv run uvicorn app.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

### 4. Frontend

```bash
cd frontend
cp .env.example .env               # NUXT_PUBLIC_API_BASE if backend isn't on :8000
pnpm install
pnpm dev
```

App at `http://localhost:3000` — four screens: **Discover** (ranked table + weight sliders + evidence popovers), **Analyze** (paste a title → normalization + score), **Compare** (niche side-by-side), **Ask** (copilot chat with tool chips).

## Verification

```bash
cd backend
uv run python scripts/eval_normalization.py    # top-1/top-3/OOC accuracy
curl localhost:8000/opportunities | head       # ranked scores with evidence
curl -X POST localhost:8000/normalize -H 'Content-Type: application/json' \
  -d '{"title":"Personalized Grandpa Gift For Father'\''s Day From Granddaughter"}'
```

## Data compliance

Only operator-assisted exports (Alura/Helium10 accounts provided by the organizers) and public
Google Trends data. No scraping, no paid credentials in the repo.

## Legacy boilerplate endpoints

`POST /documents` (RAG ingest) and `POST /chat` (RAG chat) from the original starter remain
functional but unused by the hub.
