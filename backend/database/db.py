from sqlalchemy import URL

from core.config import settings


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
