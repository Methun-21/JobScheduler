from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import create_engine
from backend.app.config import settings

# Async Engine and Sessions
async_engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Sync Engine and Sessions (used by tests or parts of worker if needed)
sync_engine = create_engine(
    settings.sync_database_url,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=sync_engine
)

Base = declarative_base()

async def get_db():
    """Dependency to retrieve database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
