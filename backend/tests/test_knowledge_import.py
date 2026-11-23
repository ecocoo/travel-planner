import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, delete, func, select, update

from backend.app.infrastructure.db.models.tables import (
    place_categories,
    places,
    route_template_stops,
)

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT / "data/knowledge"
DATES = {"start_date": "2026-12-07", "end_date": "2026-12-08"}


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


@pytest.fixture(scope="module")
def imported_catalogue(postgres_database: Engine) -> None:
    run_import(postgres_database)


@pytest.fixture
def guest_headers(api_client: httpx.Client) -> dict[str, str]:
    response = api_client.post("/api/v1/guest-sessions")
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


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


@pytest.mark.integration
@pytest.mark.usefixtures("imported_catalogue")
def test_real_catalog_generation_uses_city_and_reports_metrics(
    api_client: httpx.Client, guest_headers: dict[str, str]
) -> None:
    payload = {
        "city_id": "kazan",
        **DATES,
        "preferences": {
            "preferred_categories": ["architecture"],
            "excluded_categories": ["food"],
            "max_stops_per_day": 3,
        },
    }
    assert api_client.post("/api/v1/route-plans", json=payload).status_code == 401
    result = api_client.post("/api/v1/route-plans", headers=guest_headers, json=payload)
    assert result.status_code == 200, result.text
    plan = result.json()
    assert [day["date"] for day in plan["days"]] == list(DATES.values())
    assert plan["timezone"] == "Europe/Moscow"
    assert plan["metrics"]["scheduled_visits"] > 0
    assert plan["metrics"]["model_ms"] == 0
    assert plan["metrics"]["total_ms"] >= plan["metrics"]["routing_ms"]
    assert plan["warnings"]["unmatched_preferred_categories"] == []
    for day in plan["days"]:
        assert len(day["stops"]) <= 3
        for stop in day["stops"]:
            detail = api_client.get(f"/api/v1/places/{stop['place_id']}").json()
            assert detail["city_id"] == "kazan"
            assert "food" not in detail["categories"]


@pytest.mark.integration
@pytest.mark.usefixtures("imported_catalogue")
def test_adaptation_reads_draft_template_and_filters_requested_stops(
    api_client: httpx.Client, guest_headers: dict[str, str]
) -> None:
    template_id = "saint-petersburg-central-architecture"
    excluded = "saint-petersburg-isaakievskiy-sobor"
    path = f"/api/v1/route-templates/{template_id}/adapt"
    result = api_client.post(
        path,
        headers=guest_headers,
        json={**DATES, "preferences": {"excluded_places": [excluded]}},
    )
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["city_id"] == "saint-petersburg"
    assert plan["template"]["id"] == template_id
    assert plan["template"]["status"] == "draft"
    assert plan["template"]["route_ready"] is False
    assert excluded in plan["warnings"]["omitted_template_places"]
    assert any(
        stop["visit_mode"] == "exterior"
        for day in plan["days"]
        for stop in day["stops"]
    )
    missing = api_client.post(
        "/api/v1/route-templates/absent/adapt", headers=guest_headers, json=DATES
    )
    assert missing.status_code == 404


@pytest.mark.integration
@pytest.mark.usefixtures("imported_catalogue")
def test_rebuild_keeps_pin_through_older_client_edit_and_checks_revision_on_save(
    api_client: httpx.Client, guest_headers: dict[str, str]
) -> None:
    created = api_client.post(
        "/api/v1/trips",
        headers=guest_headers,
        json={"title": "Прогулка", "city_id": "saint-petersburg", **DATES},
    )
    assert created.status_code == 201, created.text
    trip_path = f"/api/v1/trips/{created.json()['id']}"
    itinerary_path = f"{trip_path}/itinerary"
    place_id = "saint-petersburg-isaakievskiy-sobor"
    result = api_client.put(
        itinerary_path,
        headers=guest_headers,
        json={
            "expected_revision": 1,
            "days": [
                {
                    "day_number": 2,
                    "stops": [
                        {
                            "place_id": place_id,
                            "pinned": True,
                            "visit_mode": "exterior",
                            "notes": "Личная заметка",
                        }
                    ],
                }
            ],
        },
    )
    assert result.status_code == 200, result.text
    stop_id = result.json()["days"][1]["stops"][0]["id"]
    edited = api_client.put(
        itinerary_path,
        headers=guest_headers,
        json={
            "expected_revision": 2,
            "days": [
                {
                    "day_number": 2,
                    "stops": [
                        {
                            "id": stop_id,
                            "place_id": place_id,
                            "notes": "После правки",
                        }
                    ],
                }
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["days"][1]["stops"][0]["pinned"] is True
    assert edited.json()["days"][1]["stops"][0]["visit_mode"] == "exterior"

    payload = {
        "expected_revision": 3,
        "preferences": {"excluded_places": [place_id]},
    }
    rebuild_path = f"{itinerary_path}/rebuild"
    stranger = api_client.post("/api/v1/guest-sessions").json()["access_token"]
    assert (
        api_client.post(
            rebuild_path,
            headers={"Authorization": f"Bearer {stranger}"},
            json=payload,
        ).status_code
        == 404
    )
    stale = api_client.post(
        rebuild_path, headers=guest_headers, json={**payload, "expected_revision": 2}
    )
    assert stale.status_code == 409
    result = api_client.post(rebuild_path, headers=guest_headers, json=payload)
    assert result.status_code == 200, result.text
    plan = result.json()
    (pinned,) = [stop for stop in plan["days"][1]["stops"] if stop["pinned"]]
    assert pinned["id"] == stop_id
    assert pinned["notes"] == "После правки"
    assert pinned["duration_minutes"] is None
    assert pinned["visit_mode"] == "exterior"
    assert plan["warnings"]["pinned_excluded_places"] == [place_id]
    assert api_client.get(trip_path, headers=guest_headers).json()["revision"] == 3

    fields = ("id", "place_id", "duration_minutes", "notes", "pinned", "visit_mode")
    save = {
        "expected_revision": plan["expected_revision"],
        "days": [
            {
                "day_number": day["day_number"],
                "stops": [{key: stop[key] for key in fields} for stop in day["stops"]],
            }
            for day in plan["days"]
        ],
    }
    saved = api_client.put(itinerary_path, headers=guest_headers, json=save)
    assert saved.status_code == 200, saved.text
    assert (
        api_client.put(itinerary_path, headers=guest_headers, json=save).status_code
        == 409
    )
