# Crawl API + background workers

The CLI scripts (`README_amazon.md`) crawl in the foreground. This is the same
engine behind an HTTP API, with Celery running the job and Postgres holding the
progress so a status page can poll it.

```
POST /crawl ──► crawl_sessions row (pending) ──► Celery ──► Redis
                                                    │
                              worker picks it up ───┘
                                                    │
                     phase 1 ─► crawl_keywords + crawl_products (links)
                     phase 2 ─► crawl_products (details), one commit per product
                                                    │
GET /crawl/{id} ◄── reads counters straight from Postgres
```

Progress lives in Postgres, not in Celery's result backend, so it survives a
worker restart and always matches the rows already written.

---

## Running it

```bash
# 1. infrastructure
docker compose up -d db redis

# 2. schema
cd backend && uv run alembic upgrade head

# 3. worker (leave running)
uv run celery -A app.crawl.celery_app worker --loglevel=info --concurrency=1

# 4. API
uv run uvicorn app.main:app --reload --port 8000
```

`--concurrency=1` is deliberate: each task drives its own Camoufox browser
pool, and two pools in one process fight over memory and blur the fingerprint
isolation. Scale with more worker *processes*.

Redis is on **6479** (not 6379) to avoid colliding with a local install.

---

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/crawl/locations` | valid delivery locations per storefront |
| `POST` | `/crawl` | queue a crawl, returns the session immediately |
| `GET` | `/crawl` | recent sessions, newest first |
| `GET` | `/crawl/{id}` | progress — poll this |
| `GET` | `/crawl/{id}/products` | results so far (safe mid-crawl) |
| `POST` | `/crawl/{id}/cancel` | revoke a running crawl |

### Start a crawl

```bash
curl -X POST localhost:8000/crawl -H 'Content-Type: application/json' -d '{
  "keywords": ["ceramic mug", "travel tumbler"],
  "region": "us",
  "location": "los-angeles",
  "max_products": 100,
  "options": {"sort": "newest", "min_rating": 4, "prime_only": true}
}'
```

`location` takes a preset (`los-angeles`) or a postcode (`90210`), and **must
belong to `region`** — Amazon accepts a foreign postcode, ignores it, and then
returns no prices at all. That is validated at request time:

```json
{"detail": "'SW1A1AA' is not a valid US postcode (expected something like '10001')..."}
```

Populate the UI's location picker from `GET /crawl/locations` rather than
hardcoding a list.

### Poll progress

```bash
curl localhost:8000/crawl/<id>
```

```json
{
  "status": "fetching",
  "progress": 0.55,
  "phase_detail": "fetching 100 detail pages",
  "links_found": 100, "products_done": 50, "products_failed": 0,
  "products_total": 100,
  "keyword_progress": [
    {"keyword": "ceramic mug", "links_found": 52, "pages_fetched": 2,
     "stopped_reason": "no_next_page", "error": null}
  ]
}
```

| Status | Meaning |
| --- | --- |
| `pending` | queued, no worker yet |
| `discovering` | phase 1, walking search pages |
| `fetching` | phase 2, detail pages |
| `completed` / `failed` / `cancelled` | terminal |

`progress` is 0.0–1.0. Phase 1 reports a flat `0.1` because it cannot know how
many products exist yet; inventing a percentage there would make the bar jump
backwards when the real total arrives. Poll every 2–3 s.

### Read results

`GET /crawl/{id}/products` works while the crawl is still running — rows are
committed one at a time.

Two fields are easy to misread:

* **`price` without `currency` is meaningless.** Amazon quotes the currency of
  the exit IP.
* **`bought_past_month: null` means unknown, not zero.** It is also a floor
  ("500+" → 500). `estimated_monthly_revenue` returns `null` rather than
  inventing a 0.
* **`price: null` with `unshippable: true`** means Amazon refused to show a
  price at that delivery address — not that the product is free or gone.

### Quality report

On completion the session carries the same report the CLI prints:

```json
"quality": {
  "products": 100, "failed": 2, "success_rate": 0.98,
  "coverage": {"price": 1.0, "bought_past_month": 0.87, ...},
  "currencies": {"USD": 100}, "expected_currency": "USD",
  "unshippable": 0, "mean_confidence": 0.98
},
"warnings": []
```

Show `warnings` on the status page. A run can report 100% success and still be
broken — that is exactly how the delivery-location bug stayed hidden during
development.

---

## Tables

| Table | Holds |
| --- | --- |
| `crawl_sessions` | the request + live progress + final quality report |
| `crawl_keywords` | per-keyword phase-1 outcome (`stopped_reason`, page count) |
| `crawl_products` | raw crawled products, unique per `(session_id, external_id)` |

`crawl_products` is deliberately **not** `listings`: it is the unnormalized
crawl output. `pipelines/normalize.py` maps it into taxonomy-linked listings.
Keeping the raw rows means a normalization bug is fixable without re-crawling.

Link rows are written during phase 1, then merged with detail fields in phase 2
via upsert, so a crawl that dies halfway still leaves usable rows.

---

## Config

Defaults in `app/config.py`, override in `backend/.env`:

```
REDIS_URL=redis://localhost:6479/0
CELERY_BROKER_URL=            # defaults to REDIS_URL
CELERY_RESULT_BACKEND=        # defaults to REDIS_URL
CELERY_WORKER_CONCURRENCY=1
CELERY_TASK_TIME_LIMIT=7200   # 500 products ≈ 100 min at the default rate limit
```

Crawler tuning (`CRAWLER_*`) is unchanged — see `README_amazon.md`.

---

## Operating notes

**A crawl is slow.** ~12 s per detail page at the default rate limit; 100
products ≈ 20 min. The 2 h task limit exists for 500-product runs.

**Cancel kills the worker process.** The task sits inside `asyncio.run` driving
a browser, so a cooperative signal would not be seen until the current page
finished. `revoke(terminate=True)` is the only thing that stops it promptly.
`worker_max_tasks_per_child=8` also recycles the process periodically, because
browsers leak.

**Broker down?** `POST /crawl` still returns 202, but the session is marked
`failed` with `"Could not queue the crawl"` instead of sitting `pending`
forever. Check `docker compose ps redis`.

**Stuck in `pending`?** No worker is consuming the queue. The status is stored
as plain text, so it can be inspected and corrected with SQL.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| session stays `pending` | worker not running, or pointed at another Redis |
| `failed` with `AmazonLocationError` | `location` did not apply — see `README_amazon.md` |
| all prices `null`, `unshippable: 0` | selector drift; capture a page with `scripts/test_camoufox.py` |
| all prices `null`, `unshippable: N` | delivery address cannot receive those items; pick another location |
| worker RSS climbing | expected between recycles; lower `worker_max_tasks_per_child` |
