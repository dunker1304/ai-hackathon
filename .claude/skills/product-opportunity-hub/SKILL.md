---
name: product-opportunity-hub
description: Architecture, invariants, and change seams for the Product Opportunity Hub (POD product research copilot — FastAPI + Nuxt 4 + pgvector + OpenRouter). Use when touching scoring, normalization, the Ask tool layer, the report template, seeding, or the Nuxt screens in this repo.
---

# Product Opportunity Hub

AI product-research copilot for a print-on-demand R&D team. It ingests marketplace
exports (Etsy/Alura, Amazon/Helium10) plus Google-Trends-shaped weekly series, maps
listing titles onto a 42-node Printway catalog taxonomy, and turns the aggregate into
an explainable **Opportunity Score** with a launch recommendation.

Everything below is derived from the code — treat it as the contract when changing it.

## Layout

```
backend/app/
  config.py        Settings (pydantic-settings, .env) — DB URL, OpenRouter, OpenAI, Langfuse, CORS
  db.py            SQLAlchemy engine + SessionLocal + get_db() FastAPI dependency
  llm.py           THE ONLY provider seam: get_llm(), get_embeddings(), get_langfuse_handler()
  models.py        ProductType, TaxonomyAlias, Listing, Keyword, TrendPoint, Score (+ legacy Document/Chunk)
  schemas.py       Pydantic request/response contracts
  scoring.py       Opportunity Score: 6 dimensions + fit gate + decide()
  normalize.py     4-stage title → taxonomy pipeline
  taxonomy_data.py MATERIAL_SYNONYMS / FORM_SYNONYMS / JUNK_PATTERNS (rule-layer vocabulary)
  ask.py           Ask orchestration loop → NDJSON stream
  ask_tools.py     7 deterministic tools + TOOLS registry
  report.py        Fixed Markdown report template
  routers/hub.py   All hub endpoints
  routers/{chat,documents}.py  Legacy RAG boilerplate — functional, unused by the hub
backend/scripts/
  seed.py                Idempotent load of backend/data/* → Postgres, then compute_all()
  eval_normalization.py  top-1 / top-3 / OOC accuracy over data/eval_listings.jsonl (40 cases)
  generate_mock_data.py  One-shot mock generator (data is committed; rarely re-run)
frontend/app/
  composables/useWeights.ts  Client mirror of scoring weights + decide()
  composables/useAsk.ts      NDJSON stream reader for POST /ask
  pages/{index,analyze,compare,ask}.vue   Discover / Analyze / Compare / Ask
```

## The five flows

### 1. Seed → score (offline, `scripts/seed.py`)
`TRUNCATE` hub tables → taxonomy → alias embeddings → listings → keywords → trends →
`scoring.compute_all()`. Scores are **precomputed at seed time**, never on request.
`ADAPTERS` in `seed.py` maps export column names onto the `listings` table — that dict
is the only thing to touch when swapping mock CSVs for real Alura/Helium10 exports.

Mock listing rows carry a ground-truth `_pt` column (→ `product_type_id`,
`norm_confidence=0.99`). Real exports won't have it, and must be normalized through the
pipeline instead.

### 2. Scoring (`app/scoring.py`)
Six dimensions, percentile-ranked **within category cohort** (global fallback when a
cohort has < 5 members), weighted by `DEFAULT_WEIGHTS`:

| Dimension | Weight | Raw signal |
|---|---|---|
| demand | 0.22 | `est_sales.sum() + 0.3 * favorites.sum()` |
| competition | 0.20 | `-(listing_count * avg keyword competition)` — inverted, fewer is better |
| growth | 0.20 | `polyfit` slope over the last 12 trend weeks, ÷ mean → %/week |
| seasonality | 0.12 | Launch-window bands from weeks-to-peak (not percentile-ranked) |
| personalization | 0.13 | `(60 if personalization_friendly else 10) + 40 * personalized-title share` |
| revenue | 0.13 | `(est_sales * price).sum()` |

Seasonality bands (`LAUNCH_WINDOW_BANDS`): 8–16 wks → 95, 6–8 → 75, 0–6 → 50,
16–33 → 65, peak month is now → 20.

`Score.dims` is the **single source of truth**: `{dim: {value, raw, explanation,
evidence: [{metric, value, source, fetched_at}]}}`. It powers the weight sliders,
`explain_score`, evidence popovers, freshness badges, and the report's evidence table
simultaneously. Anything added to a dimension must populate all four keys.

### 3. Normalization (`app/normalize.py`)
Four stages, each with a deterministic fallback:

1. **`extract_signature`** — LLM structured output, then **unioned** with
   `lexical_signature()` so keyword signals survive LLM paraphrasing. LLM failure →
   pure lexical.
2. **`retrieve_candidates`** — pgvector max-cosine-similarity over *all* aliases
   (251 aliases / 42 types), best-per-type, top 10. No alias index or embeddings API
   down → `_lexical_candidates()` token-overlap + form/material bonuses.
3. **`choose`** — LLM picks exactly one id **from the candidate list** or null; any id
   outside the list is rejected and falls back to `_heuristic_choice()`.
4. **`apply_rules`** — unambiguous form+material keyword match overrides the LLM
   (conf 0.92); single form match overrides a low-confidence LLM pick (0.85);
   otherwise restrict candidates to form-matching types (0.65).

Below `OOC_CONFIDENCE_THRESHOLD = 0.55` the result is `out_of_catalog` with the reason
stated — **never guess a type**. Current eval: top-1 93%, top-3 100%, OOC 100%.

### 4. Ask (`app/ask.py` + `app/ask_tools.py`)
Not text RAG. The LLM routes intent to 7 deterministic tools, then narrates:
`rank_opportunities`, `explain_score`, `normalize_listing`, `compare_niches`,
`seasonality_window`, `design_insights`, `generate_report`.

Loop: up to `MAX_TOOL_ROUNDS = 4` tool rounds, then one streamed narration pass with
**no tools bound**. History is truncated to the last 8 messages. Output is NDJSON,
one JSON object per line: `{"type": "tool"|"token"|"error"|"done", ...}` — matched by
`useAsk.ts`, so any new event type needs both sides changed.

Tool registration is one line in the `TOOLS` dict; `tool_schemas()` clones each args
model under the tool's name because `bind_tools` uses the class name as the tool name.
Every tool returns a JSON-safe dict including a `sources` list, and errors are returned
as `{"error": ...}` **to the LLM** rather than raised.

The system prompt (`ASK_SYSTEM`) mandates a closing `**→ Recommendation**` block on
every answer — what to make, which material, when to launch, top risk.

### 5. Report (`app/report.py`)
Fixed Markdown template filled entirely from DB facts. The LLM writes only the
3-sentence executive summary; if it fails, `summary_context` (already pure facts) is
used verbatim. Sections: decision header, product spec, score breakdown, fit gate,
launch timing, design direction, suggested price (market avg × 1.05), evidence table,
risks.

## Invariants — do not break these

1. **Fit is a gate, not a dimension.** `scoring.decide()`: `fit < 50` → `not_recommend`
   regardless of market score; `fit < 70` → at best `conditional`. Never fold `fit`
   into `DEFAULT_WEIGHTS`.
2. **The LLM never computes numbers.** It selects tools and narrates. Every figure in
   an answer or report traces to a tool result or a `dims` entry.
3. **Every number carries provenance.** `evidence` entries always have `source` and
   `fetched_at`. `GET /meta/freshness` exposes this per source.
4. **Honesty over coverage.** Below-threshold normalization returns `out_of_catalog`
   with the closest candidate named and the reason stated.
5. **Graceful degradation is a feature.** Every LLM/embeddings call sits behind
   `try/except` with a deterministic fallback. The app must stay useful with no
   `OPENROUTER_API_KEY` credits and no `OPENAI_API_KEY`. The `# noqa: BLE001` comments
   mark these intentional broad catches.
6. **Weights are mirrored.** `DEFAULT_WEIGHTS` and `decide()` exist in both
   `app/scoring.py` and `frontend/app/composables/useWeights.ts`. Change one → change
   the other, or the slider view diverges from the stored decision.
7. **Provider access goes through `app/llm.py`.** No router or business module imports
   `langchain_openai` directly. All three getters are `@lru_cache`d.
8. **Sliders recompute client-side.** `/opportunities` ships full `dims`; the frontend
   re-totals and re-decides locally (`index.vue` → `rows` computed). No round trip.

## Change seams

| Want to… | Touch |
|---|---|
| Ingest a real export | `ADAPTERS` in `scripts/seed.py` (same file names + column names) |
| Add/retune a dimension | `scoring.py` `DEFAULT_WEIGHTS` + `compute_all()` dims block **and** `useWeights.ts` |
| Add an Ask capability | New args model + fn in `ask_tools.py`, one entry in `TOOLS` |
| Swap the chat model | `OPENROUTER_MODEL` in `backend/.env` — no code change |
| Extend normalization vocabulary | `MATERIAL_SYNONYMS` / `FORM_SYNONYMS` in `taxonomy_data.py`, then re-run the eval |
| Change report structure | `report.build_report()` template string |
| Grow the catalog | `backend/data/taxonomy.json` + `aliases.json`, re-run `seed.py` |

## Conventions

- Backend: `from __future__ import annotations`, full type annotations, module
  docstrings that explain *why*. Business logic lives in `app/*.py`; routers stay thin
  and only validate + delegate. Sessions arrive via `Depends(get_db)`.
- Endpoints are sync `def` (sync SQLAlchemy sessions) — do not convert to `async def`
  without moving to an async engine.
- Frontend: Nuxt 4 auto-imports (no manual `useFetch`/`useState` imports), `useFetch`
  with `{ lazy: true, server: false }` for first paint, `$fetch` for event-driven
  calls, Nuxt UI + Tailwind 4 utility classes inline.
- Schema changes go through Alembic (`backend/alembic/versions/`), not raw DDL.
  `EMBEDDING_DIM = 1536` in `models.py` must stay in sync with `scripts/init_db.sql`
  and the embedding model.

## Verify

```bash
cd backend
uv run python scripts/eval_normalization.py          # top-1 / top-3 / OOC — regression gate for normalize.py
uv run python scripts/seed.py                        # full reload + rescore
curl localhost:8000/opportunities | head
curl -X POST localhost:8000/normalize -H 'Content-Type: application/json' \
  -d '{"title":"Personalized Acrylic Christmas Ornament"}'
curl localhost:8000/meta/freshness
```

There is no pytest suite yet — `eval_normalization.py` is the only automated check.
New backend logic should arrive with tests (pytest, per repo testing rules); changes to
`normalize.py` must at minimum keep the eval numbers from regressing.

## Endpoints

`GET /taxonomy` · `GET /opportunities?category&material` · `GET /compare?ids=a,b` ·
`POST /normalize` · `POST /ask` (NDJSON stream) · `GET /report/{id}` ·
`GET /meta/freshness` · `GET /health`.
Legacy: `POST /documents`, `POST /chat` (original RAG starter, unused by the hub —
leave alone unless explicitly asked).
