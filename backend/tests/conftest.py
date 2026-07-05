import os
# Force DB_NAME to job_scheduler_test before importing backend modules
os.environ["DB_NAME"] = "job_scheduler_test"

import asyncio
import pytest
import pytest_asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from backend.app.database import Base, get_db
import backend.app.database as db_module
from backend.app.main import app
from httpx import AsyncClient

# Overwrite the app's global database module engine and sessionmaker to use NullPool for testing
# This prevents asyncpg connection cleanup warnings and "Event loop is closed" errors on Windows.
db_module.async_engine = create_async_engine(
    db_module.settings.async_database_url,
    echo=False,
    poolclass=NullPool
)
db_module.AsyncSessionLocal = async_sessionmaker(
    bind=db_module.async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    policy = asyncio.get_event_loop_policy()
    res_loop = policy.new_event_loop()
    yield res_loop
    res_loop.close()

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Initializes the database schema for the test suite."""
    # Create tables using the overridden test engine
    async with db_module.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Drop tables
    async with db_module.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db_module.async_engine.dispose()

@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provides a database session for a single test and truncates tables in teardown."""
    async with db_module.AsyncSessionLocal() as session:
        yield session
        
    # Teardown: use a clean, separate session to truncate tables to avoid connection reuse conflicts
    async with db_module.AsyncSessionLocal() as clean_session:
        async with clean_session.begin():
            # Delete in reverse sorted order to satisfy foreign keys
            for table in reversed(Base.metadata.sorted_tables):
                await clean_session.execute(table.delete())
            await clean_session.commit()

import httpx

@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Provides a test HTTP client with an overridden DB session dependency."""
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
