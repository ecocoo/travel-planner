from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.infrastructure.db.models.tables import (
    auth_sessions,
    guest_sessions,
    trips,
    users,
)

PUBLIC_USER_COLUMNS = (
    users.c.id,
    users.c.name,
    users.c.email,
    users.c.role,
    users.c.created_at,
)


async def create_user(connection: AsyncConnection, values: dict) -> RowMapping | None:
    result = await connection.execute(
        pg_insert(users)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[users.c.email])
        .returning(*PUBLIC_USER_COLUMNS)
    )
    return result.mappings().one_or_none()


async def find_by_email(connection: AsyncConnection, email: str) -> RowMapping | None:
    result = await connection.execute(select(users).where(users.c.email == email))
    return result.mappings().one_or_none()


async def create_session(connection: AsyncConnection, values: dict) -> None:
    await connection.execute(insert(auth_sessions).values(**values))


async def find_session(
    connection: AsyncConnection, token_hash: str
) -> RowMapping | None:
    result = await connection.execute(
        select(*PUBLIC_USER_COLUMNS, auth_sessions.c.id.label("session_id"))
        .join(auth_sessions, auth_sessions.c.user_id == users.c.id)
        .where(
            auth_sessions.c.token_hash == token_hash,
            auth_sessions.c.expires_at > func.now(),
        )
    )
    return result.mappings().one_or_none()


async def delete_session(connection: AsyncConnection, session_id: UUID) -> None:
    await connection.execute(
        delete(auth_sessions).where(auth_sessions.c.id == session_id)
    )


async def enable_guide(connection: AsyncConnection, user_id: UUID) -> RowMapping:
    result = await connection.execute(
        update(users)
        .where(users.c.id == user_id)
        .values(role="guide")
        .returning(*PUBLIC_USER_COLUMNS)
    )
    return result.mappings().one()


async def lock_guest(connection: AsyncConnection, token_hash: str) -> RowMapping | None:
    result = await connection.execute(
        select(guest_sessions)
        .where(guest_sessions.c.token_hash == token_hash)
        .with_for_update()
    )
    return result.mappings().one_or_none()


async def transfer_trips(
    connection: AsyncConnection, guest_id: UUID, user_id: UUID
) -> int:
    result = await connection.execute(
        update(trips)
        .where(trips.c.owner_id == guest_id)
        .values(owner_id=user_id, revision=trips.c.revision + 1, updated_at=func.now())
        .returning(trips.c.id)
    )
    count = len(result.all())
    await connection.execute(
        update(guest_sessions)
        .where(guest_sessions.c.id == guest_id)
        .values(claimed_by=user_id)
    )
    return count
