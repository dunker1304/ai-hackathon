import logging
import sys

from collections.abc import AsyncGenerator, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from common.model import MappedBase
from core.config import settings

logger = logging.getLogger(__name__)


def get_database_url(*, unittest: bool = False) -> URL:
    database = settings.database_schema if not unittest else f"{settings.database_schema}_test"

    url = URL.create(
        drivername="postgresql+asyncpg",
        username=settings.database_user,
        password=settings.database_password,
        host=settings.database_host,
        port=settings.database_port,
        database=database,
    )
    return url


def create_database_async_engine(url: str | URL) -> AsyncEngine:
    try:
        return create_async_engine(
            url,
            echo=settings.database_echo,
            echo_pool=settings.database_pool_echo,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=3600,
            pool_pre_ping=True,  # turn off the pool pre-ping feature to avoid unnecessary overhead
            pool_use_lifo=False,
        )
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        sys.exit()


class DatabaseAsyncSessionMaker:
    def __init__(self, makers: Mapping[str, async_sessionmaker[AsyncSession]]) -> None:
        if "default" not in makers:
            raise ValueError("The session factory must include a default data source.")
        self._makers = dict(makers)

    def _get_maker(self, source: str) -> async_sessionmaker[AsyncSession]:
        try:
            return self._makers[source]
        except KeyError as e:
            raise ValueError(f"Unknown database data source: {source}") from e

    def __call__(self, source: str = "default", **kwargs: Any) -> AsyncSession:
        return self._get_maker(source)(**kwargs)

    def begin(self, source: str = "default") -> AbstractAsyncContextManager[AsyncSession]:
        return self._get_maker(source).begin()


def create_database_async_session(
    async_engine: AsyncEngine,
    *,
    source_binds: Mapping[str, AsyncEngine] | None = None,
) -> DatabaseAsyncSessionMaker:
    engines = dict(source_binds or {})
    engines.setdefault("default", async_engine)
    return DatabaseAsyncSessionMaker({
        source: async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        for source, engine in engines.items()
    })


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_db_session() as session:
        try:
            yield session  # ruff: ignore[yield-in-context-manager-in-async-generator]
        finally:
            await session.close()


async def get_db_transaction() -> AsyncGenerator[AsyncSession, None]:
    async with async_db_session.begin() as session:
        try:
            yield session  # ruff: ignore[yield-in-context-manager-in-async-generator]
        finally:
            await session.close()


async def create_tables() -> None:
    async with async_engine.begin() as coon:
        await coon.run_sync(MappedBase.metadata.create_all)


async def drop_tables() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(MappedBase.metadata.drop_all)


async_engine = create_database_async_engine(get_database_url())
_database_engines: dict[str, AsyncEngine] = {"default": async_engine}
async_db_session = create_database_async_session(async_engine, source_binds=_database_engines)
