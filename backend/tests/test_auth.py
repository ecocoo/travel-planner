"""Account authentication, ownership and atomic guest transfer regressions."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text, update

from backend.app.infrastructure.db.models.tables import (
    auth_sessions,
    cities,
    guest_sessions,
    places,
    trips,
    users,
)
from backend.app.security.guest_tokens import hash_guest_token
from backend.app.security.passwords import password_hasher

pytestmark = pytest.mark.integration
PREFIX = "/api/v1"
PASSWORD = "test-password-123"


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def register(client, **values):
    data = {"name": "Влад", "email": f"{uuid4().hex}@example.com", "password": PASSWORD}
    data.update(values)
    response = client.post(PREFIX + "/auth/register", json=data)
    assert response.status_code == 201, response.text
    return response.json()


def guest(client):
    response = client.post(PREFIX + "/guest-sessions")
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def catalogue(postgres_database):
    root = Path(__file__).resolve().parents[2] / "data/knowledge"
    city = json.loads((root / "cities.json").read_text())[0]
    place = next(
        p
        for p in json.loads((root / "places.json").read_text())
        if p["city_id"] == city["id"] and p["parent_place_id"] is None
    )
    with postgres_database.begin() as connection:
        connection.execute(
            insert(cities).values(
                id=city["id"],
                name=city["name"],
                timezone=city["timezone"],
                payload=city,
            )
        )
        connection.execute(
            insert(places).values(
                id=place["id"],
                city_id=city["id"],
                name=place["name"],
                entity_type=place["entity_type"],
                record_status=place["record_status"],
                route_ready=place["route_ready"],
                payload=place,
            )
        )
    return city["id"], place["id"]


def create_trip(client, token, catalogue):
    response = client.post(
        PREFIX + "/trips",
        headers=headers(token),
        json={
            "title": "Поездка",
            "city_id": catalogue[0],
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def claim(client, token, guest_token):
    return client.post(
        PREFIX + "/auth/claim-guest-trips",
        headers=headers(token),
        json={"guest_token": guest_token},
    )


@pytest.mark.parametrize("role", ["traveler", "guide"])
def test_registration_login_and_private_storage(api_client, postgres_database, role):
    email = f"{uuid4().hex}@example.com"
    result = register(api_client, email=email.upper(), role=role, name="  Влад  ")
    assert result["user"]["name"] == "Влад"
    assert result["user"]["email"] == email
    assert result["user"]["role"] == role
    assert result["transferred_trips"] == 0
    assert "password_hash" not in result["user"]
    expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=29) < expiry - datetime.now(UTC) <= timedelta(days=30)
    with postgres_database.connect() as connection:
        row = (
            connection.execute(
                select(users).where(users.c.id == UUID(result["user"]["id"]))
            )
            .mappings()
            .one()
        )
        assert row["password_hash"].startswith("$argon2id$")
        assert password_hasher.verify(PASSWORD, row["password_hash"])
        session = (
            connection.execute(
                select(auth_sessions).where(auth_sessions.c.user_id == row["id"])
            )
            .mappings()
            .one()
        )
        assert session["token_hash"] == hash_guest_token(result["access_token"])
        assert session["token_hash"] != result["access_token"]
    response = api_client.post(
        PREFIX + "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["user"] == result["user"]
    assert response.json()["access_token"] != result["access_token"]
    me = api_client.get(PREFIX + "/auth/me", headers=headers(result["access_token"]))
    assert me.status_code == 200
    assert me.json() == result["user"]


def test_email_conflict_and_invalid_login(api_client):
    user = register(api_client)
    response = api_client.post(
        PREFIX + "/auth/register",
        json={
            "name": "Другой",
            "email": user["user"]["email"].upper(),
            "password": PASSWORD,
        },
    )
    assert response.status_code == 409
    errors = []
    for email in [user["user"]["email"], "missing@example.com"]:
        response = api_client.post(
            PREFIX + "/auth/login", json={"email": email, "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        errors.append(response.json())
    assert errors[0] == errors[1]


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "invalid"},
        {"name": "  "},
        {"password": "short"},
        {"role": "admin"},
        {"extra": "forbidden"},
        {"password": "x" * 129},
    ],
)
def test_registration_validation(api_client, changes):
    data = {"name": "Влад", "email": "valid@example.com", "password": PASSWORD}
    data.update(changes)
    response = api_client.post(PREFIX + "/auth/register", json=data)
    assert response.status_code == 422
    assert all("input" not in item for item in response.json()["detail"])


def test_account_trip_isolation_and_guide_upgrade(api_client, catalogue):
    owner, stranger = register(api_client), register(api_client, role="guide")
    trip = create_trip(api_client, owner["access_token"], catalogue)
    for token in [stranger["access_token"], guest(api_client)]:
        for method, suffix, body in [
            ("GET", "", None),
            ("PATCH", "", {"expected_revision": 1, "title": "Чужая"}),
            ("PUT", "/itinerary", {"expected_revision": 1, "days": []}),
            ("DELETE", "?expected_revision=1", None),
        ]:
            response = api_client.request(
                method,
                PREFIX + "/trips/" + trip["id"] + suffix,
                headers=headers(token),
                json=body,
            )
            assert response.status_code == 404, response.text
    for _ in range(2):
        response = api_client.post(
            PREFIX + "/auth/me/guide", headers=headers(owner["access_token"])
        )
        assert response.status_code == 200
        assert response.json()["id"] == owner["user"]["id"]
        assert response.json()["role"] == "guide"
    assert (
        api_client.get(
            PREFIX + "/trips/" + trip["id"], headers=headers(owner["access_token"])
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            PREFIX + "/auth/me/guide", headers=headers(guest(api_client))
        ).status_code
        == 401
    )


def test_logout_and_expiry(api_client, postgres_database):
    user = register(api_client)
    login = api_client.post(
        PREFIX + "/auth/login",
        json={"email": user["user"]["email"], "password": PASSWORD},
    ).json()
    assert (
        api_client.post(
            PREFIX + "/auth/logout", headers=headers(user["access_token"])
        ).status_code
        == 204
    )
    for path in ["/auth/me", "/trips"]:
        assert (
            api_client.get(
                PREFIX + path, headers=headers(user["access_token"])
            ).status_code
            == 401
        )
    assert (
        api_client.get(
            PREFIX + "/auth/me", headers=headers(login["access_token"])
        ).status_code
        == 200
    )
    with postgres_database.begin() as connection:
        connection.execute(
            update(auth_sessions)
            .where(
                auth_sessions.c.token_hash == hash_guest_token(login["access_token"])
            )
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    for path in ["/auth/me", "/trips"]:
        assert (
            api_client.get(
                PREFIX + path, headers=headers(login["access_token"])
            ).status_code
            == 401
        )


@pytest.mark.parametrize("operation", ["register", "login", "claim"])
def test_guest_transfer_preserves_itinerary_and_is_idempotent(
    api_client, catalogue, operation
):
    token = guest(api_client)
    trip = create_trip(api_client, token, catalogue)
    path = PREFIX + "/trips/" + trip["id"]
    response = api_client.put(
        path + "/itinerary",
        headers=headers(token),
        json={
            "expected_revision": 1,
            "days": [
                {
                    "day_number": 1,
                    "stops": [
                        {
                            "place_id": catalogue[1],
                            "pinned": True,
                            "visit_mode": "exterior",
                            "notes": "Утро",
                            "duration_minutes": 30,
                        }
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    before = response.json()
    if operation == "register":
        account = register(api_client, guest_token=token)
        assert account["transferred_trips"] == 1
    else:
        account = register(api_client)
        if operation == "login":
            response = api_client.post(
                PREFIX + "/auth/login",
                json={
                    "email": account["user"]["email"],
                    "password": PASSWORD,
                    "guest_token": token,
                },
            )
            assert response.status_code == 200, response.text
            account = response.json()
            assert account["transferred_trips"] == 1
        else:
            assert claim(api_client, account["access_token"], token).json() == {
                "transferred_trips": 1
            }
    after = api_client.get(path, headers=headers(account["access_token"])).json()
    assert after["id"] == before["id"]
    assert after["days"] == before["days"]
    assert after["source_template"] == before["source_template"]
    assert after["revision"] == before["revision"] + 1
    assert api_client.get(path, headers=headers(token)).status_code == 401
    assert (
        api_client.post(
            PREFIX + "/trips",
            headers=headers(token),
            json={
                "title": "Поздняя",
                "city_id": catalogue[0],
                "start_date": "2026-12-01",
                "end_date": "2026-12-01",
            },
        ).status_code
        == 401
    )
    assert claim(api_client, account["access_token"], token).json() == {
        "transferred_trips": 0
    }
    stranger = register(api_client)
    assert claim(api_client, stranger["access_token"], token).status_code == 409
    assert (
        api_client.patch(
            path,
            headers=headers(account["access_token"]),
            json={"expected_revision": before["revision"], "title": "Устаревшая"},
        ).status_code
        == 409
    )


def test_invalid_transfer_rolls_back_registration_and_session(
    api_client, postgres_database
):
    email = f"{uuid4().hex}@example.com"
    data = {
        "name": "Влад",
        "email": email,
        "password": PASSWORD,
        "guest_token": "invalid",
    }
    assert api_client.post(PREFIX + "/auth/register", json=data).status_code == 401
    with postgres_database.connect() as connection:
        assert (
            connection.scalar(select(users.c.id).where(users.c.email == email)) is None
        )
    data.pop("guest_token")
    response = api_client.post(PREFIX + "/auth/register", json=data)
    assert response.status_code == 201
    with postgres_database.connect() as connection:
        count_before = connection.scalar(text("SELECT count(*) FROM auth_sessions"))
    assert (
        api_client.post(
            PREFIX + "/auth/login",
            json={"email": email, "password": PASSWORD, "guest_token": "invalid"},
        ).status_code
        == 401
    )
    with postgres_database.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM auth_sessions"))
            == count_before
        )


def test_concurrent_claims_have_one_owner(api_client, catalogue):
    token = guest(api_client)
    trip = create_trip(api_client, token, catalogue)
    accounts = [register(api_client), register(api_client)]
    barrier = threading.Barrier(2)

    def transfer(account):
        barrier.wait(timeout=5)
        return claim(api_client, account["access_token"], token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(transfer, accounts))
    assert sorted(r.status_code for r in results) == [200, 409]
    for account, result in zip(accounts, results, strict=True):
        expected = 200 if result.status_code == 200 else 404
        assert (
            api_client.get(
                PREFIX + "/trips/" + trip["id"],
                headers=headers(account["access_token"]),
            ).status_code
            == expected
        )


def test_claim_sees_guest_trip_committed_while_waiting(
    api_client, postgres_database, catalogue
):
    token = guest(api_client)
    account = register(api_client)
    with (
        postgres_database.connect() as writer,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        transaction = writer.begin()
        guest_id = writer.scalar(
            select(guest_sessions.c.id)
            .where(guest_sessions.c.token_hash == hash_guest_token(token))
            .with_for_update(read=True)
        )
        future = executor.submit(claim, api_client, account["access_token"], token)
        # Wait until the API is actually blocked on this transaction's shared lock.
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with postgres_database.connect() as observer:
                    waiting = observer.scalar(
                        text("""
                        SELECT count(*) FROM pg_stat_activity
                        WHERE wait_event_type = 'Lock' AND query LIKE '%guest_sessions%'
                    """)
                    )
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting, "Claim never waited for the guest write"
            trip_id = uuid4()
            writer.execute(
                insert(trips).values(
                    id=trip_id,
                    owner_id=guest_id,
                    city_id=catalogue[0],
                    title="Параллельная поездка",
                    start_date=datetime(2026, 12, 1).date(),
                    end_date=datetime(2026, 12, 1).date(),
                )
            )
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
        response = future.result(timeout=10)
    assert response.status_code == 200, response.text
    assert response.json()["transferred_trips"] == 1
    assert (
        api_client.get(
            PREFIX + "/trips/" + str(trip_id), headers=headers(account["access_token"])
        ).status_code
        == 200
    )
    assert api_client.get(PREFIX + "/trips", headers=headers(token)).status_code == 401


def test_database_failure_rolls_back_claim(api_client, postgres_database, catalogue):
    token = guest(api_client)
    trip = create_trip(api_client, token, catalogue)
    account = register(api_client)
    with postgres_database.begin() as connection:
        guest_id = connection.scalar(
            select(guest_sessions.c.id).where(
                guest_sessions.c.token_hash == hash_guest_token(token)
            )
        )
        # This test-only constraint rejects the owner update after the guest lock.
        connection.execute(
            text(
                "ALTER TABLE trips ADD CONSTRAINT test_reject_transfer CHECK "
                f"(id != '{UUID(trip['id'])}' OR owner_id = '{guest_id}')"
            )
        )
    try:
        response = claim(api_client, account["access_token"], token)
        assert response.status_code == 503, response.text
        with postgres_database.connect() as connection:
            assert (
                connection.scalar(
                    select(guest_sessions.c.claimed_by).where(
                        guest_sessions.c.id == guest_id
                    )
                )
                is None
            )
            row = connection.execute(
                select(trips.c.owner_id, trips.c.revision).where(
                    trips.c.id == UUID(trip["id"])
                )
            ).one()
            assert row.owner_id == guest_id
            assert row.revision == 1
        assert (
            api_client.get(
                PREFIX + "/trips/" + trip["id"], headers=headers(token)
            ).status_code
            == 200
        )
    finally:
        with postgres_database.begin() as connection:
            connection.execute(
                text("ALTER TABLE trips DROP CONSTRAINT test_reject_transfer")
            )
    assert claim(api_client, account["access_token"], token).json() == {
        "transferred_trips": 1
    }
