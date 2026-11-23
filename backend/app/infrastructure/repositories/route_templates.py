from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.infrastructure.db.models.tables import route_templates


async def find_template(
    connection: AsyncConnection, template_id: str
) -> dict[str, Any] | None:
    return await connection.scalar(
        select(route_templates.c.payload).where(route_templates.c.id == template_id)
    )
