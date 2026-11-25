from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.domain.route_templates import RouteTemplateSearch
from backend.app.infrastructure.db.models.tables import places, route_templates


async def find_template(
    connection: AsyncConnection, template_id: str
) -> dict[str, Any] | None:
    return await connection.scalar(
        select(route_templates.c.payload).where(route_templates.c.id == template_id)
    )


async def read_page(
    connection: AsyncConnection,
    filters: RouteTemplateSearch,
) -> tuple[list[dict[str, Any]], int]:
    statement = select(route_templates.c.payload)
    if filters.city_id is not None:
        statement = statement.where(route_templates.c.city_id == filters.city_id)
    if filters.status is not None:
        statement = statement.where(route_templates.c.status == filters.status)
    if filters.route_ready is not None:
        statement = statement.where(
            route_templates.c.route_ready == filters.route_ready
        )
    total = await connection.scalar(
        select(func.count()).select_from(statement.subquery())
    )
    records = await connection.scalars(
        statement.order_by(route_templates.c.name, route_templates.c.id)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return list(records), total


async def read_places(
    connection: AsyncConnection,
    place_ids: set[str],
) -> dict[str, dict[str, Any]]:
    records = await connection.execute(
        select(places.c.id, places.c.payload).where(places.c.id.in_(place_ids))
    )
    return {row.id: row.payload for row in records}
