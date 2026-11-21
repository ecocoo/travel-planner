from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def get_connection() -> Iterator[Connection]:
    # Keep the count and page contents in the same catalogue snapshot.
    with (
        get_engine()
        .connect()
        .execution_options(isolation_level="REPEATABLE READ") as connection
    ):
        yield connection
