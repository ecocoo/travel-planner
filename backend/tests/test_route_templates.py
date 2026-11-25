import copy
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Engine, delete, func, insert, select, text, update

from backend.app.api.schemas.route_templates import RouteTemplateFilters
from backend.app.domain.route_templates import copy_template_days
from backend.app.infrastructure.db.models.tables import (
    route_templates,
    trip_stops,
    trips,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ID = "saint-petersburg-central-architecture"


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"city_id": " "},
        {"status": " "},
    ],
)
def test_template_filters_reject_invalid_parameters(params: dict) -> None:
    with pytest.raises(ValueError):
        RouteTemplateFilters(**params)


def test_copy_days_preserves_order_modes_and_independent_ids() -> None:
    template = {
        "days": [
            {
                "day_number": 2,
                "stops": [
                    {
                        "order": 2,
                        "place_id": "b",
                        "duration_minutes": 25,
                        "visit_mode": "exterior",
                    },
                    {"order": 1, "place_id": "a"},
                ],
            },
            {"day_number": 1, "stops": []},
        ]
    }
    first, second = copy_template_days(template), copy_template_days(template)
    assert [day["day_number"] for day in first] == [1, 2]
    assert [stop["place_id"] for stop in first[1]["stops"]] == ["a", "b"]
    assert first[1]["stops"][0]["duration_minutes"] is None
    assert first[1]["stops"][1]["visit_mode"] == "exterior"
    assert {stop["id"] for stop in first[1]["stops"]}.isdisjoint(
        stop["id"] for stop in second[1]["stops"]
    )
    assert all(stop["pinned"] is False for stop in first[1]["stops"])


@pytest.mark.parametrize(
    "days",
    [
        [],
        [{"day_number": 0, "stops": []}],
        [{"day_number": 1, "stops": []}] * 2,
        [{"day_number": 1, "stops": [{"order": 1, "place_id": "a"}] * 2}],
    ],
)
def test_copy_days_rejects_ambiguous_template(days: list[dict]) -> None:
    with pytest.raises(ValueError):
        copy_template_days({"days": days})


@pytest.fixture(scope="module")
def imported_templates(postgres_database: Engine) -> list[dict]:
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.import_knowledge"],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": postgres_database.url.render_as_string(hide_password=False),
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(
        (ROOT / "data/knowledge/route_templates.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def guest_headers(api_client: httpx.Client) -> dict[str, str]:
    response = api_client.post("/api/v1/guest-sessions")
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
def template_factory(postgres_database: Engine, imported_templates: list[dict]):
    created_ids = []

    def create(*, days=None, **changes):
        payload = copy.deepcopy(
            next(t for t in imported_templates if t["id"] == TEMPLATE_ID)
        )
        payload.update(id="test-template-" + uuid4().hex, **changes)
        if days is not None:
            payload["days"] = days
        with postgres_database.begin() as connection:
            connection.execute(
                insert(route_templates).values(
                    **{
                        key: payload[key]
                        for key in ("id", "city_id", "name", "status", "route_ready")
                    },
                    payload=payload,
                )
            )
        created_ids.append(payload["id"])
        return payload

    yield create
    with postgres_database.begin() as connection:
        connection.execute(
            delete(route_templates).where(route_templates.c.id.in_(created_ids))
        )


@pytest.mark.integration
@pytest.mark.usefixtures("imported_templates")
def test_public_template_list_filters_and_pagination(api_client: httpx.Client) -> None:
    response = api_client.get("/api/v1/route-templates", params={"limit": 200})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 48
    assert len(result["items"]) == 48
    assert all(t["status"] == "draft" and not t["route_ready"] for t in result["items"])
    first = api_client.get("/api/v1/route-templates?limit=2").json()
    second = api_client.get("/api/v1/route-templates?limit=2&offset=2").json()
    assert first["total"] == second["total"] == 48
    assert [t["id"] for t in first["items"] + second["items"]] == [
        t["id"] for t in result["items"][:4]
    ]
    for params, expected in [
        ({"city_id": "kazan", "status": "draft", "route_ready": "false"}, 3),
        ({"city_id": "missing"}, 0),
        ({"route_ready": "true"}, 0),
        ({"status": "published"}, 0),
    ]:
        page = api_client.get("/api/v1/route-templates", params=params).json()
        assert page["total"] == expected
    assert api_client.get("/api/v1/route-templates?offset=9999").json()["items"] == []
    assert api_client.get("/api/v1/route-templates?limit=0").status_code == 422


@pytest.mark.integration
@pytest.mark.usefixtures("imported_templates")
def test_public_template_cards_preserve_sources_and_stops(
    api_client: httpx.Client,
    imported_templates: list[dict],
) -> None:
    for original in imported_templates:
        response = api_client.get("/api/v1/route-templates/" + original["id"])
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["source_urls"] == original["source_urls"]
        assert detail["validation_notes"] == original["validation_notes"]
        assert detail["walking_minutes"] == original["walking_minutes"]
        assert detail["distance_meters"] == original["distance_meters"]
        assert detail["stops_count"] == sum(
            len(day["stops"]) for day in original["days"]
        )
        assert detail["days_count"] == max(
            day["day_number"] for day in original["days"]
        )
        assert detail["attribution"]["license"] == "ODbL-1.0"
        for day, source_day in zip(detail["days"], original["days"], strict=True):
            for stop, source_stop in zip(
                day["stops"], source_day["stops"], strict=True
            ):
                assert {key: stop[key] for key in source_stop} == source_stop
                assert stop["place"]["id"] == stop["place_id"]
    assert api_client.get("/api/v1/route-templates/missing").status_code == 404


@pytest.mark.integration
@pytest.mark.usefixtures("imported_templates")
def test_copy_creates_owned_trip_with_snapshot_and_new_stop_ids(
    api_client: httpx.Client,
    guest_headers: dict[str, str],
) -> None:
    path = f"/api/v1/route-templates/{TEMPLATE_ID}"
    template = api_client.get(path).json()
    assert (
        api_client.post(path + "/copy", json={"start_date": "2026-12-07"}).status_code
        == 401
    )
    copies = []
    for _ in range(2):
        response = api_client.post(
            path + "/copy", headers=guest_headers, json={"start_date": "2026-12-07"}
        )
        assert response.status_code == 201, response.text
        trip = response.json()
        assert trip["title"] == template["name"]
        assert trip["city_id"] == template["city_id"]
        assert trip["revision"] == 1
        assert trip["end_date"] == "2026-12-07"
        assert trip["source_template"] == {
            key: template[key] for key in ("id", "name", "status", "route_ready")
        }
        assert (
            api_client.get("/api/v1/trips/" + trip["id"], headers=guest_headers).json()
            == trip
        )
        for stop, source in zip(
            trip["days"][0]["stops"], template["days"][0]["stops"], strict=True
        ):
            assert stop["place"]["id"] == source["place_id"]
            assert stop["duration_minutes"] == source["duration_minutes"]
            assert stop["visit_mode"] == source["visit_mode"]
            assert stop["pinned"] is False
        copies.append(trip)
    assert copies[0]["id"] != copies[1]["id"]
    assert {s["id"] for s in copies[0]["days"][0]["stops"]}.isdisjoint(
        s["id"] for s in copies[1]["days"][0]["stops"]
    )
    stranger = api_client.post("/api/v1/guest-sessions").json()["access_token"]
    for method, suffix, body in [
        ("GET", "", None),
        ("PATCH", "", {"expected_revision": 1, "title": "Чужая"}),
        ("PUT", "/itinerary", {"expected_revision": 1, "days": []}),
        ("DELETE", "?expected_revision=1", None),
    ]:
        response = api_client.request(
            method,
            "/api/v1/trips/" + copies[0]["id"] + suffix,
            headers={"Authorization": "Bearer " + stranger},
            json=body,
        )
        assert response.status_code == 404


@pytest.mark.integration
def test_copy_dates_and_multiday_template(
    api_client: httpx.Client,
    guest_headers: dict[str, str],
    template_factory,
) -> None:
    template = template_factory()
    days = copy.deepcopy(template["days"])
    days.append({"day_number": 2, "stops": [copy.deepcopy(days[0]["stops"][0])]})
    template = template_factory(days=days)
    path = f"/api/v1/route-templates/{template['id']}/copy"
    for body in [
        {"start_date": "2026-12-07", "end_date": "2026-12-07"},
        {"start_date": "2026-12-07", "end_date": "2026-12-06"},
        {"start_date": "2026-12-07", "title": "  "},
        {"start_date": "2026-12-07", "city_id": "kazan"},
        {"start_date": "9999-12-31"},
    ]:
        result = api_client.post(path, headers=guest_headers, json=body)
        assert result.status_code == 422, result.text
    for end_date, count in [(None, 2), ("2026-12-10", 4)]:
        result = api_client.post(
            path,
            headers=guest_headers,
            json={
                "start_date": "2026-12-07",
                "end_date": end_date,
                "title": "Мой маршрут",
            },
        )
        assert result.status_code == 201, result.text
        trip = result.json()
        assert trip["title"] == "Мой маршрут"
        assert len(trip["days"]) == count
        assert trip["days"][1]["date"] == "2026-12-08"
        assert trip["days"][-1]["date"] == str(
            date(2026, 12, 7) + timedelta(days=count - 1)
        )
        if count == 4:
            assert trip["days"][-1]["stops"] == []
    result = api_client.post(
        "/api/v1/route-templates/missing/copy",
        headers=guest_headers,
        json={"start_date": "2026-12-07"},
    )
    assert result.status_code == 404


@pytest.mark.integration
def test_copied_trip_is_independent_of_template_edits_and_deletion(
    api_client: httpx.Client,
    guest_headers: dict[str, str],
    template_factory,
    postgres_database: Engine,
) -> None:
    template = template_factory()
    response = api_client.post(
        f"/api/v1/route-templates/{template['id']}/copy",
        headers=guest_headers,
        json={"start_date": "2026-12-07"},
    )
    assert response.status_code == 201, response.text
    copied = response.json()
    path = "/api/v1/trips/" + copied["id"]
    with postgres_database.begin() as connection:
        changed = {**template, "name": "Обновлённый шаблон", "route_ready": True}
        connection.execute(
            update(route_templates)
            .where(route_templates.c.id == template["id"])
            .values(name=changed["name"], route_ready=True, payload=changed)
        )
    assert api_client.get(path, headers=guest_headers).json() == copied
    edited = api_client.put(
        path + "/itinerary",
        headers=guest_headers,
        json={"expected_revision": 1, "days": []},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2
    assert edited.json()["source_template"] == copied["source_template"]
    assert (
        api_client.put(
            path + "/itinerary",
            headers=guest_headers,
            json={"expected_revision": 1, "days": []},
        ).status_code
        == 409
    )
    assert api_client.get(f"/api/v1/route-templates/{template['id']}").json()[
        "stops_count"
    ] == len(template["days"][0]["stops"])
    with postgres_database.begin() as connection:
        connection.execute(
            delete(route_templates).where(route_templates.c.id == template["id"])
        )
    assert (
        api_client.get(path, headers=guest_headers).json()["source_template"]
        == copied["source_template"]
    )


@pytest.mark.integration
def test_copy_rolls_back_trip_and_stops_on_database_error(
    api_client: httpx.Client,
    guest_headers: dict[str, str],
    postgres_database: Engine,
    imported_templates: list[dict],
) -> None:
    with postgres_database.begin() as connection:
        trip_count = connection.scalar(select(func.count()).select_from(trips))
        stop_count = connection.scalar(select(func.count()).select_from(trip_stops))
        connection.execute(
            text(
                "ALTER TABLE trip_stops ADD CONSTRAINT copy_failure "
                "CHECK (notes <> '') NOT VALID"
            )
        )
    try:
        result = api_client.post(
            f"/api/v1/route-templates/{TEMPLATE_ID}/copy",
            headers=guest_headers,
            json={"start_date": "2026-12-07"},
        )
        assert result.status_code == 503, result.text
        with postgres_database.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(trips)) == trip_count
            )
            assert (
                connection.scalar(select(func.count()).select_from(trip_stops))
                == stop_count
            )
    finally:
        with postgres_database.begin() as connection:
            connection.execute(
                text("ALTER TABLE trip_stops DROP CONSTRAINT copy_failure")
            )


@pytest.mark.integration
def test_copy_rejects_missing_and_foreign_city_places_atomically(
    api_client: httpx.Client,
    guest_headers: dict[str, str],
    template_factory,
    imported_templates: list[dict],
    postgres_database: Engine,
) -> None:
    foreign = next(t for t in imported_templates if t["city_id"] == "kazan")["days"][0][
        "stops"
    ][0]["place_id"]
    for place_id in ["missing-place", foreign]:
        template = template_factory()
        template["days"][0]["stops"][0]["place_id"] = place_id
        with postgres_database.begin() as connection:
            connection.execute(
                update(route_templates)
                .where(route_templates.c.id == template["id"])
                .values(payload=template)
            )
        before = api_client.get("/api/v1/trips", headers=guest_headers).json()["total"]
        result = api_client.post(
            f"/api/v1/route-templates/{template['id']}/copy",
            headers=guest_headers,
            json={"start_date": "2026-12-07"},
        )
        assert result.status_code == 422, result.text
        assert (
            api_client.get("/api/v1/trips", headers=guest_headers).json()["total"]
            == before
        )


@pytest.mark.integration
@pytest.mark.usefixtures("imported_templates")
def test_simultaneous_copies_are_separate_trips(
    api_client: httpx.Client,
    guest_headers: dict[str, str],
) -> None:
    def copy_route(_):
        return api_client.post(
            f"/api/v1/route-templates/{TEMPLATE_ID}/copy",
            headers=guest_headers,
            json={"start_date": "2026-12-07"},
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(copy_route, range(2)))
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json()["id"] != results[1].json()["id"]
