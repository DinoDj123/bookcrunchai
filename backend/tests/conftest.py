"""Fixtures for tests that need a real PostgreSQL database with pgvector.

They run only when TEST_DATABASE_URL is set, and they drop and recreate the schema in that database,
so it must be a separate, throwaway database, never the development one. CI sets it; locally:

    docker compose exec postgres createdb -U bookcrunch bookcrunch_test
    export TEST_DATABASE_URL=postgresql+asyncpg://bookcrunch:bookcrunch@localhost:5432/bookcrunch_test
    pytest
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    """Alembic pointed at the test database; skips every test that uses it when none is set."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


@pytest.fixture(scope="session")
def migrated_database(alembic_config: Config) -> Iterator[str]:
    """The test database with every migration applied, starting from an empty schema."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    yield alembic_config.get_main_option("sqlalchemy.url")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def connection(migrated_database: str) -> AsyncIterator[AsyncConnection]:
    """One connection inside a transaction that is rolled back after the test, so tests never see
    each other's rows."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        yield conn
        if transaction.is_active:  # a failed statement in the test may have ended it already
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """The test's own session, for setting rows up and reading them back."""
    async with AsyncSession(bind=connection, expire_on_commit=False) as db_session:
        yield db_session


@pytest.fixture
def session_factory(connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """For code that opens and commits its own sessions (the worker). Its sessions share the test's
    connection, so they see the test's rows; a commit only releases a savepoint, and everything is
    still rolled back after the test."""
    return async_sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
