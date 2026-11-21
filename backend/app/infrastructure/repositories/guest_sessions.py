from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.infrastructure.db.models.tables import guest_sessions


async def create(connection: AsyncConnection, guest_id: UUID, token_hash: str) -> None:
    await connection.execute(
        insert(guest_sessions).values(id=guest_id, token_hash=token_hash)
    )


async def find_id(connection: AsyncConnection, token_hash: str) -> UUID | None:
    return await connection.scalar(
        select(guest_sessions.c.id).where(guest_sessions.c.token_hash == token_hash)
    )
