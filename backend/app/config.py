from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-sonnet-4.5"

    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    cors_origins: str = "http://localhost:3000"

    # --- Celery / Redis ---
    # Port 6479 matches docker-compose (shifted to avoid clashing with a local
    # Redis on 6379).
    redis_url: str = "redis://localhost:6479/0"
    #: Falls back to redis_url when unset; split them to isolate the queue from
    #: task results in production.
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    #: One crawl per worker process: each task drives its own Camoufox pool,
    #: and two browser pools in one process fight over memory and fingerprints.
    celery_worker_concurrency: int = 1
    #: Hard ceiling per crawl. A 500-product run takes ~100 min at the default
    #: rate limit, so this is deliberately generous.
    celery_task_time_limit: int = 7200
    celery_task_soft_time_limit: int = 7000

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    # ScraperAPI (Amazon crawl — scripts/crawl_amazon.py). Optional: the app
    # itself never calls it; only the offline crawl script does.
    scraperapi_key: str | None = None


settings = Settings()
