"""Integration checks in a temporary PostgreSQL schema and a separate HTTP server."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from app.tables import cities, places

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("TRAVEL_TEST_DATABASE_URL"),
    "Set TRAVEL_TEST_DATABASE_URL to run PostgreSQL integration checks",
)
class TripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TRAVEL_TEST_DATABASE_URL"])
        cls.addClassCleanup(cls.engine.dispose)
        cls.schema = "trip_tests_" + uuid4().hex
        with cls.engine.begin() as connection:
            connection.execute(CreateSchema(cls.schema))
        cls.addClassCleanup(cls.drop_schema)
        url = make_url(os.environ["TRAVEL_TEST_DATABASE_URL"]).update_query_dict(
            {"options": "-csearch_path=" + cls.schema}
        )
        cls.database_url = url.render_as_string(hide_password=False)
        env = {**os.environ, "DATABASE_URL": cls.database_url}
        for command in ["upgrade head", "check"]:
            subprocess.run(
                [sys.executable, "-m", "alembic", *command.split()],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        city_records = json.loads((ROOT / "data/knowledge/cities.json").read_text())
        place_records = json.loads((ROOT / "data/knowledge/places.json").read_text())
        cls.city_id, cls.other_city = [city["id"] for city in city_records[:2]]
        selected = [
            p
            for p in place_records
            if p["city_id"] == cls.city_id and p["parent_place_id"] is None
        ][:3]
        selected += [
            p
            for p in place_records
            if p["city_id"] == cls.other_city and p["parent_place_id"] is None
        ][:1]
        cls.place_ids = [p["id"] for p in selected[:3]]
        cls.other_place = selected[-1]["id"]
        with cls.engine.begin() as connection:
            connection = connection.execution_options(
                schema_translate_map={None: cls.schema}
            )
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
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        cls.log = tempfile.TemporaryFile()
        cls.addClassCleanup(cls.log.close)
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=cls.log,
            stderr=cls.log,
        )
        cls.addClassCleanup(cls.stop_server)
        for attempt in range(100):
            try:
                with urlopen(cls.base + "/health/live", timeout=1):
                    return
            except URLError:
                if cls.server.poll() is not None:
                    break
                time.sleep(0.1)
        cls.log.seek(0)
        raise RuntimeError("Test server failed to start: " + cls.log.read().decode())

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)

    @classmethod
    def drop_schema(cls):
        with cls.engine.begin() as connection:
            connection.execute(DropSchema(cls.schema, cascade=True))

    def request(self, method, path, token=None, body=None):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = Request(
            self.base + "/api/v1" + path, data=data, headers=headers, method=method
        )
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None

    def guest(self):
        status, data = self.request("POST", "/guest-sessions")
        self.assertEqual(status, 201)
        return data["access_token"]

    def create_trip(self, token):
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

    def test_owner_isolation(self):
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

    def test_itinerary_reorder_move_and_delete(self):
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

    def test_invalid_itinerary_preserves_existing_state(self):
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

    def test_dates_and_metadata(self):
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

    def test_concurrent_edits_only_one_wins(self):
        owner = self.guest()
        trip = self.create_trip(owner)
        path = "/trips/" + trip["id"]
        barrier = threading.Barrier(2)

        def save(place_id):
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

    def test_database_failure_rolls_back_deleted_stops(self):
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
                    f"""ALTER TABLE "{self.schema}".trip_stops ADD CONSTRAINT test_failure CHECK (notes <> '__force_failure__')"""
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
                    text(
                        f'ALTER TABLE "{self.schema}".trip_stops DROP CONSTRAINT test_failure'
                    )
                )


if __name__ == "__main__":
    unittest.main()
