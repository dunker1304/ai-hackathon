from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import DateTime, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class MappedBase(AsyncAttrs, DeclarativeBase):
    @declared_attr.directive
    def __tablename__(self) -> str:
        return self.__name__.lower()

    @declared_attr.directive
    def __table_args__(self) -> dict:
        return {"comment": self.__doc__ or ""}

    __abstract__ = True


class UUIDPrimaryKeyMixin:
    """Provide a UUIDv7 primary key."""

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
    )


class TimestampMixin:
    """Provide creation and update timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
