import argparse
import importlib.util
import json
from pathlib import Path

from sqlalchemy import Connection, Table, delete, text
from sqlalchemy.dialects.postgresql import insert

from app.database import get_engine
from app.tables import (
    categories,
    cities,
    place_categories,
    places,
    route_template_stops,
    route_templates,
)


def upsert(connection: Connection, table: Table, rows: list[dict]) -> None:
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
    connection.execute(statement, rows)


def import_knowledge(directory: Path) -> dict[str, int]:
    # Reuse the catalogue's full validation before opening a write transaction.
    spec = importlib.util.spec_from_file_location(
        "knowledge_validator", directory / "validate_knowledge.py"
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    validator.validate()

    def read(name: str) -> list[dict]:
        return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))

    city_records = read("cities")
    category_records = read("categories")
    place_records = read("places")
    route_records = read("route_templates")
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
    with get_engine().begin() as connection:
        # Serialize concurrent imports, including replacement of child rows.
        connection.execute(text("SELECT pg_advisory_xact_lock(7612948301)"))
        upsert(
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
        upsert(connection, categories, category_records)
        upsert(connection, places, place_rows)
        connection.execute(
            delete(place_categories).where(
                place_categories.c.place_id.in_([p["id"] for p in place_records])
            )
        )
        if category_links:
            connection.execute(insert(place_categories), category_links)
        upsert(connection, route_templates, route_rows)
        connection.execute(
            delete(route_template_stops).where(
                route_template_stops.c.route_id.in_([r["id"] for r in route_records])
            )
        )
        if stops:
            connection.execute(insert(route_template_stops), stops)
    return {
        "cities": len(city_records),
        "categories": len(category_records),
        "places": len(place_records),
        "route_templates": len(route_records),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import the researched travel catalogue into PostgreSQL"
    )
    parser.add_argument("--directory", type=Path, default=Path("data/knowledge"))
    arguments = parser.parse_args()
    try:
        print(
            json.dumps(
                import_knowledge(arguments.directory.resolve()), ensure_ascii=False
            )
        )
    finally:
        get_engine().dispose()


if __name__ == "__main__":
    main()
