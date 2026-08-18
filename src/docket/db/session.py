"""Async engine + session factory."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from docket.config import DB_SCHEMA, settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"server_settings": {"search_path": f"{DB_SCHEMA},public"}},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session, committing on success."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
