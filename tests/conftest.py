import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.config import Settings
from shared.database.connection import Base


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        postgres_host=os.environ.get("TEST_POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("TEST_POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("TEST_POSTGRES_DB", "gds_test"),
        postgres_user=os.environ.get("TEST_POSTGRES_USER", "gds"),
        postgres_password=os.environ.get("TEST_POSTGRES_PASSWORD", "gds"),
        rabbitmq_host="localhost",
        rabbitmq_user="guest",
        rabbitmq_password="guest",
        rabbitmq_vhost="/",
        minio_endpoint="localhost:9000",
        minio_access_key="minioadmin",
        minio_secret_key="minioadmin",
        log_level="DEBUG",
        environment="development",
    )


@pytest_asyncio.fixture(scope="session")
async def async_engine(test_settings: Settings):
    engine = create_async_engine(
        test_settings.database_url,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()
