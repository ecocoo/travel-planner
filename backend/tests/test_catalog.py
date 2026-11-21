import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, insert

from backend.app.api.schemas.catalog import PlaceFilters
from backend.app.infrastructure.db.models.tables import (
    categories,
    cities,
    place_categories,
    places,
)

BOUNDS = {"min_lat": 55.0, "max_lat": 56.0, "min_lon": 49.0, "max_lon": 50.0}
ENTRANCE = {
    "latitude": 55.0,
    "longitude": 49.0,
    "kind": "entrance",
    "entrance_verified": True,
    "status": "verified",
}
SOURCES = [{"url": "https://example.org/place", "title": "Исходная карточка"}]


@pytest.fixture(scope="module")
def catalogue(postgres_database: Engine) -> None:
    records = [
        ("a-match", "sample", "food", "Кафе 100%_", ENTRANCE),
        (
            "b-match",
            "sample",
            "food",
            "Кафе 100%_",
            {**ENTRANCE, "status": "conflicting"},
        ),
        ("wildcard-decoy", "sample", "food", "Кафе 100XY", None),
        ("other-city", "other", "food", "Кафе 100%_", None),
        ("other-category", "sample", "sights", "Кафе 100%_", None),
        ("outside-bounds", "sample", "sights", "Парк", {**ENTRANCE, "latitude": 57.0}),
    ]
    with postgres_database.begin() as connection:
        connection.execute(
            insert(cities),
            [
                {"id": city, "name": city, "timezone": "UTC", "payload": {}}
                for city in ("sample", "other", "empty")
            ],
        )
        connection.execute(
            insert(categories),
            [{"id": category, "name": category} for category in ("food", "sights")],
        )
        for place_id, city_id, category_id, name, location in records:
            fields = {
                "id": place_id,
                "city_id": city_id,
                "name": name,
                "entity_type": "restaurant",
                "record_status": "verified",
                "route_ready": False,
            }
            connection.execute(
                insert(places).values(
                    **fields,
                    latitude=location["latitude"] if location else None,
                    longitude=location["longitude"] if location else None,
                    payload={
                        **fields,
                        "description": "Место в каталоге",
                        "categories": [category_id],
                        "address": None,
                        "location": location,
                        "unresolved_fields": [],
                        "visit_mode": "walk_in",
                        "area": "",
                        "parent_place_id": None,
                        "opening_hours": {},
                        "visit_duration": {},
                        "admission": {},
                        "seasonality": {},
                        "accessibility": {},
                        "official_url": None,
                        "official_url_status": "unknown",
                        "detail_url": None,
                        "sources": SOURCES,
                        "checked_on": "2026-01-15",
                        "research_status": "reviewed",
                    },
                )
            )
            connection.execute(
                insert(place_categories).values(
                    place_id=place_id, category_id=category_id
                )
            )


@pytest.mark.parametrize(
    "params",
    [
        {"min_lat": 55.0},
        {**BOUNDS, "max_lat": 54.0},
        {**BOUNDS, "has_coordinates": False},
    ],
    ids=["incomplete-bounds", "reversed-bounds", "coordinates-excluded"],
)
def test_place_filters_reject_invalid_bounds(params: dict[str, float | bool]) -> None:
    with pytest.raises(ValidationError):
        PlaceFilters.model_validate(params)


@pytest.mark.integration
@pytest.mark.usefixtures("catalogue")
def test_search_combines_filters_and_keeps_total_before_pagination(
    api_client: httpx.Client,
) -> None:
    response = api_client.get(
        "/api/v1/places",
        params={
            "city_id": "sample",
            "category_id": "food",
            "q": "кАФЕ 100%_",
            "limit": 1,
            "offset": 1,
        },
    )

    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 2
    assert (page["limit"], page["offset"]) == (1, 1)
    assert [place["id"] for place in page["items"]] == ["b-match"]


@pytest.mark.integration
@pytest.mark.usefixtures("catalogue")
def test_map_includes_only_usable_coordinates_in_bounds(
    api_client: httpx.Client,
) -> None:
    response = api_client.get("/api/v1/places/map", params=BOUNDS)

    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    [marker] = page["items"]
    assert marker["id"] == "a-match"
    assert marker["location"] == ENTRANCE
    assert page["attribution"]["license"] == "ODbL-1.0"


@pytest.mark.integration
@pytest.mark.usefixtures("catalogue")
def test_place_details_preserve_sources_and_extra_fields(
    api_client: httpx.Client,
) -> None:
    response = api_client.get("/api/v1/places/a-match")

    assert response.status_code == 200, response.text
    assert response.json()["sources"] == SOURCES
    assert response.json()["research_status"] == "reviewed"


@pytest.mark.integration
def test_unknown_place_returns_not_found(api_client: httpx.Client) -> None:
    response = api_client.get("/api/v1/places/unknown-place")

    assert response.status_code == 404
    assert response.json() == {"detail": "Place not found"}


@pytest.mark.integration
@pytest.mark.usefixtures("catalogue")
def test_city_list_counts_places_including_empty_cities(
    api_client: httpx.Client,
) -> None:
    response = api_client.get("/api/v1/cities")

    assert response.status_code == 200, response.text
    assert {city["id"]: city["places_count"] for city in response.json()} == {
        "empty": 0,
        "other": 1,
        "sample": 5,
    }
