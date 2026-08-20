"""
Single place that wires the LLM gateway (OpenRouter) and observability (Langfuse)
together. Everything else in the app calls get_llm() / get_embeddings() instead of
touching provider SDKs directly — swap models or providers here without touching
any router or business logic.
"""

from functools import lru_cache

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from core.config import settings


@lru_cache
def get_langfuse_handler() -> CallbackHandler | None:
    """Returns a LangChain callback that sends traces to Langfuse.
    Every .invoke()/.stream() call that receives this handler shows up
    in the Langfuse dashboard automatically — no manual logging needed."""
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    return CallbackHandler()


@lru_cache
def get_llm() -> ChatOpenAI:
    """Chat model routed through OpenRouter. Change OPENROUTER_MODEL in .env
    to swap models (Claude, GPT, Gemini, open-weights) — no code change here."""
    return ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        streaming=True,
        temperature=0.2,
    )


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    """OpenRouter doesn't serve embeddings, so this hits OpenAI directly.
    Keep EMBEDDING_MODEL in sync with the vector() dimension in init_db.sql."""
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )
