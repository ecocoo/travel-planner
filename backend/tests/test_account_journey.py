"""Account tokens work through template copying, planning, saving and rebuilding."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_account_planning_journey(api_client, postgres_database):
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.import_knowledge"],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": postgres_database.url.render_as_string(hide_password=False),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    account = api_client.post(
        "/api/v1/auth/register",
        json={
            "name": "Путешественник",
            "email": "journey@example.com",
            "password": "journey-password",
        },
    )
    assert account.status_code == 201, account.text
    auth = {"Authorization": "Bearer " + account.json()["access_token"]}
    dates = {"start_date": "2026-12-07", "end_date": "2026-12-08"}
    template = "/api/v1/route-templates/saint-petersburg-central-architecture"
    copied = api_client.post(template + "/copy", headers=auth, json=dates)
    assert copied.status_code == 201, copied.text
    trip_path = "/api/v1/trips/" + copied.json()["id"]
    for path, payload in [
        ("/api/v1/route-plans", {"city_id": "saint-petersburg", **dates}),
        (template + "/adapt", dates),
        (trip_path + "/itinerary/rebuild", {"expected_revision": 1}),
    ]:
        response = api_client.post(path, headers=auth, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["metrics"]["scheduled_visits"] > 0
    plan = response.json()
    days = [
        {
            "day_number": day["day_number"],
            "stops": [
                {
                    key: stop[key]
                    for key in (
                        "id",
                        "place_id",
                        "duration_minutes",
                        "notes",
                        "pinned",
                        "visit_mode",
                    )
                }
                for stop in day["stops"]
            ],
        }
        for day in plan["days"]
    ]
    saved = api_client.put(
        trip_path + "/itinerary",
        headers=auth,
        json={"expected_revision": 1, "days": days},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["source_template"] == copied.json()["source_template"]
    assert saved.json()["revision"] == 2
    assert (
        api_client.delete(
            trip_path, headers=auth, params={"expected_revision": 2}
        ).status_code
        == 204
    )
    assert api_client.get(trip_path, headers=auth).status_code == 404
