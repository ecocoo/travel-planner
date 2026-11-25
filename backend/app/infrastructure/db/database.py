from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from backend.app.config.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )


async def get_connection() -> AsyncIterator[AsyncConnection]:
    # The page count and contents must come from the same snapshot.
    async with get_engine().connect() as connection:
        yield await connection.execution_options(isolation_level="REPEATABLE READ")


async def get_transaction() -> AsyncIterator[AsyncConnection]:
    async with get_engine().connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        async with connection.begin():
            yield connection


async def get_auth_transaction() -> AsyncIterator[AsyncConnection]:
    # Claiming waits for guest writes and must then see their committed trips.
    async with get_engine().connect() as connection:
        await connection.execution_options(isolation_level="READ COMMITTED")
        async with connection.begin():
            yield connection
