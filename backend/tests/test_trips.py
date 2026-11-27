"""Integration coverage for the guest trip API."""

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Engine, insert, text

from backend.app.infrastructure.db.models.tables import cities, places

pytestmark = pytest.mark.integration
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "data/knowledge"


@pytest.fixture(scope="class")
def trip_catalogue(
    request: pytest.FixtureRequest, postgres_database: Engine, api_client: httpx.Client
) -> None:
    test_class = request.cls
    test_class.engine = postgres_database
    test_class.client = api_client
    city_records = json.loads(
        (KNOWLEDGE_DIR / "cities.json").read_text(encoding="utf-8")
    )
    place_records = json.loads(
        (KNOWLEDGE_DIR / "places.json").read_text(encoding="utf-8")
    )
    test_class.city_id, test_class.other_city = [
        city["id"] for city in city_records[:2]
    ]
    selected = [
        p
        for p in place_records
        if p["city_id"] == test_class.city_id and p["parent_place_id"] is None
    ][:3]
    selected += [
        p
        for p in place_records
        if p["city_id"] == test_class.other_city and p["parent_place_id"] is None
    ][:1]
    test_class.place_ids = [p["id"] for p in selected[:3]]
    test_class.other_place = selected[-1]["id"]
    with postgres_database.begin() as connection:
        connection.execute(
            insert(cities),
            [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "timezone": c["timezone"],
                    "payload": c,
                }
                for c in city_records[:2]
            ],
        )
        connection.execute(
            insert(places),
            [
                {
                    "id": p["id"],
                    "city_id": p["city_id"],
                    "name": p["name"],
                    "entity_type": p["entity_type"],
                    "record_status": p["record_status"],
                    "route_ready": p["route_ready"],
                    "payload": p,
                }
                for p in selected
            ],
        )


@pytest.mark.usefixtures("trip_catalogue")
class TripTests(unittest.TestCase):
    def request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        response = self.client.request(
            method, "/api/v1" + path, headers=headers, json=body
        )
        return response.status_code, response.json() if response.content else None

    def guest(self) -> str:
        response = self.client.post("/api/v1/guest-sessions")
        assert response.status_code == 201, response.text
        assert response.headers["Cache-Control"] == "no-store"
        data = response.json()
        assert data["token_type"] == "bearer"
        return data["access_token"]

    def create_trip(self, token: str) -> dict[str, Any]:
        status, data = self.request(
            "POST",
            "/trips",
            token,
            {
                "title": "Поездка",
                "city_id": self.city_id,
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
            },
        )
        self.assertEqual(status, 201, data)
        return data

    def test_owner_isolation(self) -> None:
        owner, stranger = self.guest(), self.guest()
        trip = self.create_trip(owner)
        path = "/trips/" + trip["id"]
        self.assertEqual(self.request("GET", "/trips")[0], 401)
        self.assertEqual(self.request("GET", "/trips", "invalid")[0], 401)
        self.assertEqual(self.request("GET", "/trips", owner)[1]["total"], 1)
        self.assertEqual(self.request("GET", "/trips", stranger)[1]["total"], 0)
        for method, suffix, body in [
            ("GET", "", None),
            ("PATCH", "", {"expected_revision": 1, "title": "Чужая"}),
            ("PUT", "/itinerary", {"expected_revision": 1, "days": []}),
            ("DELETE", "?expected_revision=1", None),
        ]:
            self.assertEqual(
                self.request(method, path + suffix, stranger, body)[0], 404
            )
        self.assertEqual(self.request("GET", path, owner)[1]["revision"], 1)

    def _saved_itinerary(self) -> tuple[str, str, dict[str, Any]]:
        owner = self.guest()
        trip_path = "/trips/" + self.create_trip(owner)["id"]
        status, saved = self.request(
            "PUT",
            f"{trip_path}/itinerary",
            owner,
            {
                "expected_revision": 1,
                "days": [
                    {
                        "day_number": 1,
                        "stops": [
                            {
                                "place_id": self.place_ids[0],
                                "duration_minutes": 45,
                                "notes": "Встреча в 10:00",
                                "visit_mode": "indoor",
                            },
                            {"place_id": self.place_ids[1], "pinned": True},
                        ],
                    },
                    {
                        "day_number": 2,
                        "stops": [{"place_id": self.place_ids[1]}],
                    },
                ],
            },
        )
        assert status == 200, saved
        return owner, trip_path, saved

    def test_replaces_only_the_requested_stop(self) -> None:
        owner, trip_path, before = self._saved_itinerary()
        target, neighbor = before["days"][0]["stops"]
        status, saved = self.request(
            "PATCH",
            f"{trip_path}/itinerary/stops/{target['id']}",
            owner,
            {"expected_revision": before["revision"], "place_id": self.place_ids[2]},
        )

        assert status == 200, saved
        assert saved["revision"] == before["revision"] + 1
        changed, untouched = saved["days"][0]["stops"]
        assert changed["place"]["id"] == self.place_ids[2]
        assert changed["visit_mode"] is None
        for key in ("id", "position", "duration_minutes", "notes"):
            assert changed[key] == target[key]
        assert untouched == neighbor
        assert saved["days"][1] == before["days"][1]
        assert self.request("GET", trip_path, owner)[1] == saved

    def test_removes_one_stop_and_compacts_positions_in_its_day(self) -> None:
        owner, trip_path, before = self._saved_itinerary()
        target, neighbor = before["days"][0]["stops"]
        stop_path = f"{trip_path}/itinerary/stops/{target['id']}"
        status, saved = self.request(
            "DELETE", f"{stop_path}?expected_revision={before['revision']}", owner
        )

        assert status == 200, saved
        assert saved["revision"] == before["revision"] + 1
        assert saved["days"][0]["stops"] == [{**neighbor, "position": 1}]
        assert saved["days"][1] == before["days"][1]
        assert self.request("GET", trip_path, owner)[1] == saved

    def test_targeted_edits_reject_pinned_stop(self) -> None:
        owner, trip_path, before = self._saved_itinerary()
        pinned = before["days"][0]["stops"][1]
        stop_path = f"{trip_path}/itinerary/stops/{pinned['id']}"
        status, _ = self.request(
            "PATCH",
            stop_path,
            owner,
            {"expected_revision": before["revision"], "place_id": self.place_ids[2]},
        )
        assert status == 409
        status, _ = self.request(
            "DELETE", f"{stop_path}?expected_revision={before['revision']}", owner
        )
        assert status == 409
        assert self.request("GET", trip_path, owner)[1] == before

    def test_replacing_stop_requires_a_place_in_the_trip_city(self) -> None:
        owner, trip_path, before = self._saved_itinerary()
        target = before["days"][0]["stops"][0]
        status, _ = self.request(
            "PATCH",
            f"{trip_path}/itinerary/stops/{target['id']}",
            owner,
            {"expected_revision": before["revision"], "place_id": self.other_place},
        )

        assert status == 422
        assert self.request("GET", trip_path, owner)[1] == before

    def test_targeted_edits_require_the_current_revision_and_owner(self) -> None:
        owner, trip_path, before = self._saved_itinerary()
        target = before["days"][0]["stops"][0]
        stop_path = f"{trip_path}/itinerary/stops/{target['id']}"
        current = f"{stop_path}?expected_revision={before['revision']}"

        assert self.request("DELETE", current, self.guest())[0] == 404
        assert (
            self.request("DELETE", f"{stop_path}?expected_revision=1", owner)[0] == 409
        )
        assert self.request("GET", trip_path, owner)[1] == before

    def test_itinerary_reorder_move_and_delete(self) -> None:
        owner = self.guest()
        trip = self.create_trip(owner)
        path = "/trips/" + trip["id"]
        self.assertEqual(len(trip["days"]), 2)
        status, saved = self.request(
            "PUT",
            path + "/itinerary",
            owner,
            {
                "expected_revision": 1,
                "days": [
                    {
                        "day_number": 1,
                        "stops": [{"place_id": p} for p in self.place_ids],
                    }
                ],
            },
        )
        self.assertEqual(status, 200, saved)
        stops = saved["days"][0]["stops"]
        moved = [
            {"id": s["id"], "place_id": s["place"]["id"], "duration_minutes": 30}
            for s in reversed(stops)
        ]
        status, saved = self.request(
            "PUT",
            path + "/itinerary",
            owner,
            {
                "expected_revision": 2,
                "days": [{"day_number": 2, "stops": moved}],
            },
        )
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["revision"], 3)
        self.assertEqual(saved["days"][0]["stops"], [])
        self.assertEqual(
            [s["id"] for s in saved["days"][1]["stops"]], [s["id"] for s in moved]
        )
        self.assertEqual([s["position"] for s in saved["days"][1]["stops"]], [1, 2, 3])
        self.assertEqual(
            self.request("DELETE", path + "?expected_revision=2", owner)[0], 409
        )
        self.assertEqual(
            self.request("DELETE", path + "?expected_revision=3", owner)[0], 204
        )
        self.assertEqual(self.request("GET", path, owner)[0], 404)

    def test_invalid_itinerary_preserves_existing_state(self) -> None:
        owner = self.guest()
        trip = self.create_trip(owner)
        path = "/trips/" + trip["id"]
        _, before = self.request(
            "PUT",
            path + "/itinerary",
            owner,
            {
                "expected_revision": 1,
                "days": [{"day_number": 2, "stops": [{"place_id": self.place_ids[0]}]}],
            },
        )
        repeated_id = str(uuid4())
        invalid_days = [
            [{"day_number": 3, "stops": []}],
            [{"day_number": 1, "stops": [{"place_id": "missing"}]}],
            [{"day_number": 1, "stops": [{"place_id": self.other_place}]}],
            [{"day_number": 1, "stops": []}, {"day_number": 1, "stops": []}],
            [
                {
                    "day_number": 1,
                    "stops": [{"id": repeated_id, "place_id": self.place_ids[0]}] * 2,
                }
            ],
            [
                {
                    "day_number": 1,
                    "stops": [{"place_id": self.place_ids[0], "duration_minutes": 0}],
                }
            ],
        ]
        for days in invalid_days:
            status, _ = self.request(
                "PUT",
                path + "/itinerary",
                owner,
                {"expected_revision": 2, "days": days},
            )
            self.assertEqual(status, 422, days)
            self.assertEqual(self.request("GET", path, owner)[1], before)
        for changes in [{"end_date": "2026-12-01"}, {"city_id": self.other_city}]:
            self.assertEqual(
                self.request("PATCH", path, owner, {"expected_revision": 2, **changes})[
                    0
                ],
                409,
            )
        self.assertEqual(self.request("GET", path, owner)[1], before)

    def test_dates_and_metadata(self) -> None:
        owner = self.guest()
        for overrides in [
            {"title": "  "},
            {"city_id": "missing"},
            {"end_date": "2026-11-30"},
            {"end_date": "2027-12-02"},
        ]:
            status, _ = self.request(
                "POST",
                "/trips",
                owner,
                {
                    "title": "Поездка",
                    "city_id": self.city_id,
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-02",
                    **overrides,
                },
            )
            self.assertEqual(status, 422)
        trip = self.create_trip(owner)
        path = "/trips/" + trip["id"]
        self.assertEqual(
            self.request("PATCH", path, owner, {"expected_revision": 1, "title": None})[
                0
            ],
            422,
        )
        status, changed = self.request(
            "PATCH",
            path,
            owner,
            {
                "expected_revision": 1,
                "title": "Новый план",
                "city_id": self.other_city,
                "start_date": "2027-01-01",
                "end_date": "2027-01-03",
            },
        )
        self.assertEqual(status, 200, changed)
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["days"][-1]["date"], "2027-01-03")
        self.assertEqual(
            self.request("GET", "/trips?limit=1&offset=100", owner)[1]["items"], []
        )

    def test_concurrent_edits_only_one_wins(self) -> None:
        owner = self.guest()
        trip = self.create_trip(owner)
        path = "/trips/" + trip["id"]
        barrier = threading.Barrier(2)

        def save(place_id: str) -> tuple[int, Any]:
            barrier.wait(timeout=5)
            return self.request(
                "PUT",
                path + "/itinerary",
                owner,
                {
                    "expected_revision": 1,
                    "days": [{"day_number": 1, "stops": [{"place_id": place_id}]}],
                },
            )

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, self.place_ids[:2]))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        winner = next(body for status, body in results if status == 200)
        self.assertEqual(self.request("GET", path, owner)[1], winner)

    def test_database_failure_rolls_back_deleted_stops(self) -> None:
        owner = self.guest()
        trip = self.create_trip(owner)
        path = "/trips/" + trip["id"]
        _, before = self.request(
            "PUT",
            path + "/itinerary",
            owner,
            {
                "expected_revision": 1,
                "days": [{"day_number": 1, "stops": [{"place_id": self.place_ids[0]}]}],
            },
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE trip_stops "
                    "ADD CONSTRAINT test_failure CHECK (notes <> '__force_failure__')"
                )
            )
        try:
            status, _ = self.request(
                "PUT",
                path + "/itinerary",
                owner,
                {
                    "expected_revision": 2,
                    "days": [
                        {
                            "day_number": 1,
                            "stops": [
                                {
                                    "place_id": self.place_ids[1],
                                    "notes": "__force_failure__",
                                }
                            ],
                        }
                    ],
                },
            )
            self.assertEqual(status, 503)
            self.assertEqual(self.request("GET", path, owner)[1], before)
        finally:
            with self.engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE trip_stops DROP CONSTRAINT test_failure")
                )
