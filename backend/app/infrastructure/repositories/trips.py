from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.infrastructure.db.models.tables import places, trip_stops, trips


async def find_owned(
    connection: AsyncConnection, trip_id: UUID, guest_id: UUID, *, lock: bool = True
) -> RowMapping | None:
    statement = select(trips).where(trips.c.id == trip_id, trips.c.owner_id == guest_id)
    result = await connection.execute(
        statement.with_for_update() if lock else statement
    )
    return result.mappings().first()


async def list_stops(
    connection: AsyncConnection, trip_id: UUID
) -> Sequence[RowMapping]:
    result = await connection.execute(
        select(trip_stops, places.c.payload.label("place"))
        .join(places, trip_stops.c.place_id == places.c.id)
        .where(trip_stops.c.trip_id == trip_id)
        .order_by(trip_stops.c.day_number, trip_stops.c.position)
    )
    return result.mappings().all()


async def list_stops_for_same_day(
    connection: AsyncConnection, trip_id: UUID, stop_id: UUID
) -> Sequence[RowMapping]:
    day_number = (
        select(trip_stops.c.day_number)
        .where(trip_stops.c.trip_id == trip_id, trip_stops.c.id == stop_id)
        .scalar_subquery()
    )
    result = await connection.execute(
        select(trip_stops)
        .where(trip_stops.c.trip_id == trip_id, trip_stops.c.day_number == day_number)
        .order_by(trip_stops.c.position)
    )
    return result.mappings().all()


async def create(
    connection: AsyncConnection, trip_id: UUID, guest_id: UUID, data: dict[str, Any]
) -> RowMapping:
    result = await connection.execute(
        insert(trips).values(id=trip_id, owner_id=guest_id, **data).returning(trips)
    )
    return result.mappings().one()


async def list_owned(
    connection: AsyncConnection, guest_id: UUID, limit: int, offset: int
) -> tuple[Sequence[RowMapping], int]:
    statement = select(trips).where(trips.c.owner_id == guest_id)
    total = await connection.scalar(
        select(func.count()).select_from(statement.subquery())
    )
    result = await connection.execute(
        statement.order_by(trips.c.created_at.desc(), trips.c.id)
        .limit(limit)
        .offset(offset)
    )
    return result.mappings().all(), total


async def has_incompatible_stops(
    connection: AsyncConnection, trip_id: UUID, day_count: int, city_id: str
) -> bool:
    stop_id = await connection.scalar(
        select(trip_stops.c.id)
        .join(places, trip_stops.c.place_id == places.c.id)
        .where(
            trip_stops.c.trip_id == trip_id,
            (trip_stops.c.day_number > day_count) | (places.c.city_id != city_id),
        )
        .limit(1)
    )
    return stop_id is not None


async def find_places_in_city(
    connection: AsyncConnection, place_ids: set[str], city_id: str
) -> set[str]:
    return set(
        await connection.scalars(
            select(places.c.id).where(
                places.c.id.in_(place_ids), places.c.city_id == city_id
            )
        )
    )


async def has_foreign_stop(
    connection: AsyncConnection, trip_id: UUID, stop_ids: list[UUID]
) -> bool:
    stop_id = await connection.scalar(
        select(trip_stops.c.id)
        .where(trip_stops.c.id.in_(stop_ids), trip_stops.c.trip_id != trip_id)
        .limit(1)
    )
    return stop_id is not None


async def replace_stops(
    connection: AsyncConnection,
    trip_id: UUID,
    rows: list[dict[str, Any]],
    *,
    day_number: int | None = None,
) -> None:
    statement = delete(trip_stops).where(trip_stops.c.trip_id == trip_id)
    if day_number is not None:
        statement = statement.where(trip_stops.c.day_number == day_number)
    await connection.execute(statement)
    if rows:
        await connection.execute(insert(trip_stops), rows)


async def read_stop_preferences(
    connection: AsyncConnection, trip_id: UUID, stop_ids: list[UUID]
) -> dict[UUID, RowMapping]:
    result = await connection.execute(
        select(
            trip_stops.c.id,
            trip_stops.c.pinned,
            trip_stops.c.visit_mode,
            trip_stops.c.place_id,
        ).where(
            trip_stops.c.trip_id == trip_id,
            trip_stops.c.id.in_(stop_ids),
        )
    )
    return {row["id"]: row for row in result.mappings()}


async def update_trip(
    connection: AsyncConnection,
    trip_id: UUID,
    revision: int,
    changes: dict[str, Any],
) -> RowMapping:
    result = await connection.execute(
        update(trips)
        .where(trips.c.id == trip_id)
        .values(**changes, revision=revision + 1, updated_at=func.now())
        .returning(trips)
    )
    return result.mappings().one()


async def remove(connection: AsyncConnection, trip_id: UUID) -> None:
    await connection.execute(delete(trips).where(trips.c.id == trip_id))
