"""Upgrade older guest data and preserve trips across a schema rollback."""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text

from backend.app.infrastructure.db.models.tables import cities, trips, users
from backend.app.security.guest_tokens import hash_guest_token

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def migrate(database, target, direction="upgrade"):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/alembic.ini",
            direction,
            *([target] if target else []),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": database.url.render_as_string(hide_password=False),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_migration_keeps_guest_ids_tokens_and_account_owned_trips(postgres_database):
    migrate(postgres_database, "4a6f9d10b2c3", "downgrade")
    guest_id, guest_trip, account_id, account_trip = [uuid4() for _ in range(4)]
    token_hash = hash_guest_token("existing-guest-token")
    with postgres_database.begin() as connection:
        connection.execute(
            insert(cities).values(
                id="migration-city", name="Город", timezone="Europe/Moscow", payload={}
            )
        )
        connection.execute(
            text("INSERT INTO guest_sessions (id, token_hash) VALUES (:id, :token)"),
            {"id": guest_id, "token": token_hash},
        )
        connection.execute(
            insert(trips).values(
                id=guest_trip,
                owner_id=guest_id,
                city_id="migration-city",
                title="Старая поездка",
                start_date=date(2026, 12, 1),
                end_date=date(2026, 12, 1),
            )
        )
    migrate(postgres_database, "head")
    with postgres_database.begin() as connection:
        assert (
            connection.scalar(select(users.c.id).where(users.c.id == guest_id))
            == guest_id
        )
        assert (
            connection.scalar(
                text("SELECT token_hash FROM guest_sessions WHERE id=:id"),
                {"id": guest_id},
            )
            == token_hash
        )
        assert (
            connection.scalar(select(trips.c.owner_id).where(trips.c.id == guest_trip))
            == guest_id
        )
        connection.execute(
            insert(users).values(
                id=account_id,
                name="Влад",
                email="migration@example.com",
                password_hash="test-hash",
            )
        )
        connection.execute(
            insert(trips).values(
                id=account_trip,
                owner_id=account_id,
                city_id="migration-city",
                title="Поездка аккаунта",
                start_date=date(2026, 12, 1),
                end_date=date(2026, 12, 1),
            )
        )
    migrate(postgres_database, "4a6f9d10b2c3", "downgrade")
    with postgres_database.connect() as connection:
        assert set(connection.scalars(select(trips.c.id))) == {guest_trip, account_trip}
        assert (
            connection.scalar(
                text("SELECT token_hash FROM guest_sessions WHERE id=:id"),
                {"id": guest_id},
            )
            == token_hash
        )
        assert (
            connection.scalar(
                text("SELECT id FROM guest_sessions WHERE id=:id"), {"id": account_id}
            )
            == account_id
        )
    migrate(postgres_database, "head")
    migrate(postgres_database, "", "check")
