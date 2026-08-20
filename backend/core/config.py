from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_schema: str
    database_host: str
    database_port: int
    database_user: str
    database_password: str
    database_echo: bool = False
    database_pool_echo: bool = False

    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-sonnet-4.5"

    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    cors_origins: str = "http://localhost:3000"


settings = Settings()
