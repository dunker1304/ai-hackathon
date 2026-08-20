import asyncio

from logging.config import fileConfig

import sqlalchemy as sa

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from common.model import MappedBase
from database.db import get_database_url
from utils.dynamic_import import get_all_models

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _register_model_globals() -> None:
    for model_obj in get_all_models():
        model_name = model_obj.name if isinstance(model_obj, sa.Table) else model_obj.__name__
        if model_name not in globals():
            globals()[model_name] = model_obj


_register_model_globals()

target_metadata = MappedBase.metadata

config.set_main_option(
    "sqlalchemy.url",
    get_database_url().render_as_string(hide_password=False).replace("%", "%%"),
)


def render_item(type_, obj, autogen_context) -> bool:  # ruff: ignore[missing-type-function-argument]
    # Kiểm tra xem đối tượng hoặc kiểu dữ liệu đang render có thuộc pgvector không
    if type_ in ("type", "model") and "pgvector" in getattr(obj, "__module__", ""):
        # Thêm 'import pgvector' vào tập hợp các imports của file migration này
        autogen_context.imports.add("import pgvector")

    return False


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    def process_revision_directives(context, revision, directives) -> None:  # ruff: ignore[missing-type-function-argument]
        if config.cmd_opts.autogenerate:
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                print("\nNo changes in model detected")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
        process_revision_directives=process_revision_directives,
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
