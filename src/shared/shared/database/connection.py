from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from shared.config import Settings


class Base(DeclarativeBase):
    pass


def create_engine(settings: "Settings"):
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


def make_session_factory(settings: "Settings") -> async_sessionmaker[AsyncSession]:
    engine = create_engine(settings)
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory(settings: "Settings") -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory(settings)
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    from shared.config import get_settings

    factory = get_session_factory(get_settings())
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
