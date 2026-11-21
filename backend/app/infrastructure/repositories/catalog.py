from collections.abc import Sequence
from typing import Any

from sqlalchemy import RowMapping, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.domain.catalog import PlaceSearch
from backend.app.infrastructure.db.models.tables import (
    categories,
    cities,
    place_categories,
    places,
)


async def list_cities(connection: AsyncConnection) -> Sequence[RowMapping]:
    statement = (
        select(
            cities.c.id,
            cities.c.name,
            cities.c.timezone,
            func.count(places.c.id).label("places_count"),
        )
        .select_from(cities.outerjoin(places))
        .group_by(cities.c.id)
        .order_by(cities.c.name, cities.c.id)
    )
    return (await connection.execute(statement)).mappings().all()


async def list_categories(connection: AsyncConnection) -> Sequence[RowMapping]:
    result = await connection.execute(
        select(categories).order_by(categories.c.name, categories.c.id)
    )
    return result.mappings().all()


def filtered_places(filters: PlaceSearch, *, map_only: bool = False) -> Select:
    statement = select(places.c.id, places.c.payload)
    if filters.city_id is not None:
        statement = statement.where(places.c.city_id == filters.city_id)
    if filters.category_id is not None:
        matching_category = (
            select(place_categories.c.place_id)
            .where(
                place_categories.c.place_id == places.c.id,
                place_categories.c.category_id == filters.category_id,
            )
            .exists()
        )
        statement = statement.where(matching_category)
    if filters.entity_type is not None:
        statement = statement.where(places.c.entity_type == filters.entity_type)
    if filters.route_ready is not None:
        statement = statement.where(places.c.route_ready == filters.route_ready)
    if filters.q is not None:
        # Use Unicode collation for Cyrillic even in databases with the C locale.
        searchable = [
            places.c.name,
            places.c.payload["description"].astext,
            places.c.payload["address"].astext,
        ]
        statement = statement.where(
            or_(
                *(
                    column.collate("pg_unicode_fast").icontains(
                        filters.q, autoescape=True
                    )
                    for column in searchable
                )
            )
        )
    usable_coordinates = and_(
        places.c.latitude.is_not(None),
        places.c.longitude.is_not(None),
        places.c.payload["location"]["status"].astext.is_distinct_from("conflicting"),
    )
    if map_only or filters.min_lat is not None:
        statement = statement.where(usable_coordinates)
    if filters.has_coordinates is not None:
        statement = statement.where(usable_coordinates == filters.has_coordinates)
    if filters.min_lat is not None:
        statement = statement.where(
            places.c.latitude.between(filters.min_lat, filters.max_lat),
            places.c.longitude.between(filters.min_lon, filters.max_lon),
        )
    return statement


async def read_page(
    connection: AsyncConnection, filters: PlaceSearch, *, map_only: bool = False
) -> tuple[list[dict[str, Any]], int]:
    statement = filtered_places(filters, map_only=map_only)
    total = await connection.scalar(
        select(func.count()).select_from(statement.subquery())
    )
    records = await connection.execute(
        statement.order_by(places.c.name, places.c.id)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return [record.payload for record in records], total


async def find_place(
    connection: AsyncConnection, place_id: str
) -> dict[str, Any] | None:
    return await connection.scalar(
        select(places.c.payload).where(places.c.id == place_id)
    )


async def city_exists(connection: AsyncConnection, city_id: str) -> bool:
    return (
        await connection.scalar(select(cities.c.id).where(cities.c.id == city_id))
        is not None
    )


async def check_ready(connection: AsyncConnection) -> None:
    await connection.execute(select(1).select_from(places).limit(1))
