"""Async engine + session factory."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from docket import events
from docket.config import DB_SCHEMA, settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"server_settings": {"search_path": f"{DB_SCHEMA},public"}},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session, committing on success.

    Stream events are published here rather than where they are created, and
    only after the commit has landed. A move announced before its transaction
    committed could still roll back, and a subscriber cannot un-see one.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
            events.publish_staged(session)
        except Exception:
            await session.rollback()
            events.discard_staged(session)
            raise
