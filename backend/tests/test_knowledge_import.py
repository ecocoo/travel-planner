import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select, update

from backend.app.infrastructure.db.models.tables import (
    place_categories,
    places,
    route_template_stops,
)

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT / "data/knowledge"


def run_import(database: Engine) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.scripts.import_knowledge",
            "--directory",
            str(KNOWLEDGE_DIR),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": database.url.render_as_string(hide_password=False),
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_reimport_restores_place_and_route_links_without_duplicates(
    postgres_database: Engine,
) -> None:
    place_records = json.loads(
        (KNOWLEDGE_DIR / "places.json").read_text(encoding="utf-8")
    )
    routes = json.loads(
        (KNOWLEDGE_DIR / "route_templates.json").read_text(encoding="utf-8")
    )
    place = next(place for place in place_records if place["categories"])
    route = next(
        route for route in routes if any(day["stops"] for day in route["days"])
    )
    place_row = places.c.id == place["id"]
    category_links = place_categories.c.place_id == place["id"]
    route_links = route_template_stops.c.route_id == route["id"]

    run_import(postgres_database)
    with postgres_database.begin() as connection:
        connection.execute(update(places).where(place_row).values(name="Outdated name"))
        connection.execute(delete(place_categories).where(category_links))
        connection.execute(delete(route_template_stops).where(route_links))

    run_import(postgres_database)

    with postgres_database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(places)) == len(
            place_records
        )
        assert (
            connection.scalar(select(places.c.name).where(place_row)) == place["name"]
        )
        assert set(
            connection.scalars(
                select(place_categories.c.category_id).where(category_links)
            )
        ) == set(place["categories"])
        restored_stops = connection.execute(
            select(
                route_template_stops.c.day_number,
                route_template_stops.c.position,
                route_template_stops.c.place_id,
            ).where(route_links)
        )
        assert set(restored_stops) == {
            (day["day_number"], stop["order"], stop["place_id"])
            for day in route["days"]
            for stop in day["stops"]
        }
