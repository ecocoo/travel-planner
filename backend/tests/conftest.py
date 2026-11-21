import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_database() -> Iterator[Engine]:
    dsn = os.environ.get("TRAVEL_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TRAVEL_TEST_DATABASE_URL to run PostgreSQL integration checks")

    schema = f"travel_tests_{uuid4().hex}"
    url = make_url(dsn).update_query_dict({"options": f"-csearch_path={schema}"})
    database_url = url.render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        try:
            for command in (("upgrade", "head"), ("check",)):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "alembic",
                        "-c",
                        "backend/alembic.ini",
                        *command,
                    ],
                    cwd=ROOT,
                    env={**os.environ, "DATABASE_URL": database_url},
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert result.returncode == 0, result.stderr
            yield engine
        finally:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def api_client(postgres_database: Engine) -> Iterator[httpx.Client]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    database_url = postgres_database.url.render_as_string(hide_password=False)
    with tempfile.TemporaryFile() as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--loop",
                "asyncio:SelectorEventLoop",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env={**os.environ, "DATABASE_URL": database_url},
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False
            ) as client:
                for _ in range(100):
                    try:
                        client.get("/health/live", timeout=1).raise_for_status()
                    except httpx.HTTPError:
                        if server.poll() is not None:
                            break
                        time.sleep(0.1)
                    else:
                        yield client
                        return
                log.seek(0)
                startup_log = log.read().decode(errors="replace")
                pytest.fail(f"Test server failed to start: {startup_log}")
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=10)
