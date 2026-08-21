"""Celery application.

Start a worker from backend/:

    uv run celery -A app.crawl.celery_app worker --loglevel=info --concurrency=1

Concurrency is 1 by design: every crawl task drives its own Camoufox browser
pool, and two pools inside one process compete for memory and blur the
fingerprint isolation the anti-detect browser is there to provide. Scale by
running more worker *processes*, not more threads.
"""

from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "hackathon",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.crawl.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Progress lives in Postgres; Redis only needs to hold the final state
    # long enough for a client to poll it.
    result_expires=3600,
    task_track_started=True,
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    worker_concurrency=settings.celery_worker_concurrency,
    # A crawl is long and side-effectful. Acking late would re-run the whole
    # thing after a worker crash; acking early loses at most one session, which
    # is already recorded as FAILED in Postgres.
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    # Browsers leak; recycling the process every few crawls keeps RSS flat.
    worker_max_tasks_per_child=8,
)
