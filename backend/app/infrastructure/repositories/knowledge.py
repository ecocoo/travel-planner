from typing import Any

from sqlalchemy import Table, delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.infrastructure.db.models.tables import (
    categories,
    cities,
    place_categories,
    places,
    route_template_stops,
    route_templates,
)


async def _upsert(
    connection: AsyncConnection, table: Table, rows: list[dict[str, Any]]
) -> None:
    if not rows:
        return
    statement = insert(table)
    updates = {
        column.name: statement.excluded[column.name]
        for column in table.columns
        if not column.primary_key
    }
    statement = statement.on_conflict_do_update(
        index_elements=list(table.primary_key.columns),
        set_=updates,
    )
    await connection.execute(statement, rows)


async def import_records(
    connection: AsyncConnection,
    city_records: list[dict[str, Any]],
    category_records: list[dict[str, Any]],
    place_records: list[dict[str, Any]],
    route_records: list[dict[str, Any]],
) -> None:
    place_rows = []
    category_links = []
    for place in place_records:
        location = place["location"] or {}
        place_rows.append(
            {
                "id": place["id"],
                "city_id": place["city_id"],
                "name": place["name"],
                "entity_type": place["entity_type"],
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "parent_place_id": place["parent_place_id"],
                "record_status": place["record_status"],
                "route_ready": place["route_ready"],
                "payload": place,
            }
        )
        category_links.extend(
            {"place_id": place["id"], "category_id": category}
            for category in place["categories"]
        )
    route_rows = [
        {
            **{
                key: route[key]
                for key in ("id", "city_id", "name", "status", "route_ready")
            },
            "payload": route,
        }
        for route in route_records
    ]
    stops = [
        {
            "route_id": route["id"],
            "day_number": day["day_number"],
            "position": stop["order"],
            "place_id": stop["place_id"],
        }
        for route in route_records
        for day in route["days"]
        for stop in day["stops"]
    ]
    # Serialize concurrent imports, including replacement of child rows.
    await connection.execute(text("SELECT pg_advisory_xact_lock(7612948301)"))
    await _upsert(
        connection,
        cities,
        [
            {
                "id": city["id"],
                "name": city["name"],
                "timezone": city["timezone"],
                "payload": city,
            }
            for city in city_records
        ],
    )
    await _upsert(connection, categories, category_records)
    await _upsert(connection, places, place_rows)
    await connection.execute(
        delete(place_categories).where(
            place_categories.c.place_id.in_([p["id"] for p in place_records])
        )
    )
    if category_links:
        await connection.execute(insert(place_categories), category_links)
    await _upsert(connection, route_templates, route_rows)
    await connection.execute(
        delete(route_template_stops).where(
            route_template_stops.c.route_id.in_([r["id"] for r in route_records])
        )
    )
    if stops:
        await connection.execute(insert(route_template_stops), stops)
