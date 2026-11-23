import json
from asyncio import run
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.api.dependencies import get_preview_guest, get_route_generation_service
from backend.app.application.generation import RouteGenerationService
from backend.app.config.config import Settings
from backend.app.infrastructure.repositories import catalog
from backend.app.main import create_app

REQUEST = {"city_id": "kazan", "start_date": "2026-12-07", "end_date": "2026-12-07"}
type ProviderHandler = Callable[[httpx.Request], httpx.Response]
type PreviewRequest = Callable[[ProviderHandler, dict[str, Any]], httpx.Response]


@pytest.fixture
def preview_client(
    monkeypatch: pytest.MonkeyPatch,
) -> PreviewRequest:
    records = [
        {
            "id": id_,
            "name": name,
            "categories": [category],
            "opening_hours": {"weekly": None},
            "location": {"latitude": 55.8, "longitude": lon, "status": status},
            "visit_mode": "indoor",
            "seasonality": {"months": None},
            "admission": {"timed_entry": False, "booking_required": False},
            "visit_duration": {"minutes": 30},
            "route_ready": False,
        }
        for id_, name, category, lon, status in [
            ("museum", "Музей", "art", 49.1, "verified"),
            ("park", "Парк", "walking", 49.11, "verified"),
            ("bad", "Кафе", "food", 49.12, "conflicting"),
        ]
    ]
    monkeypatch.setattr(catalog, "list_city_places", AsyncMock(return_value=records))
    monkeypatch.setattr(catalog, "find_city_timezone", AsyncMock(return_value="UTC"))
    monkeypatch.setattr(
        catalog,
        "list_categories",
        AsyncMock(
            return_value=[{"id": k, "name": k} for k in ("art", "walking", "food")]
        ),
    )

    async def post(handler: ProviderHandler, payload: dict[str, Any]) -> httpx.Response:
        app = create_app()
        app.dependency_overrides[get_preview_guest] = uuid4
        open_snapshots = 0

        @asynccontextmanager
        async def connect() -> AsyncIterator[AsyncConnection]:
            nonlocal open_snapshots
            open_snapshots += 1
            try:
                yield cast(AsyncConnection, object())
            finally:
                open_snapshots -= 1

        def check_snapshot_closed(request: httpx.Request) -> httpx.Response:
            assert open_snapshots == 0
            return handler(request)

        async def service() -> AsyncIterator[RouteGenerationService]:
            transport = httpx.MockTransport(check_snapshot_closed)
            async with httpx.AsyncClient(transport=transport) as provider:
                yield RouteGenerationService(
                    connect, provider, Settings(osrm_url="http://osrm.test")
                )

        app.dependency_overrides[get_route_generation_service] = service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post("/api/v1/route-plans", json=payload)

    def request(handler: ProviderHandler, payload: dict[str, Any]) -> httpx.Response:
        return run(post(handler, payload))

    return request


def test_text_preferences_use_ollama_and_directed_walking_matrix(
    preview_client: PreviewRequest,
) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            assert payload["stream"] is False
            assert payload["format"]["additionalProperties"] is False
            assert set(json.loads(payload["messages"][1]["content"])["categories"]) == {
                "art",
                "walking",
                "food",
            }
            content = {
                "preferred_categories": ["art"],
                "day_start": "10:00",
                "unhandled_requests": ["Вход бесплатный"],
            }
            return httpx.Response(
                200, json={"message": {"content": json.dumps(content)}}
            )
        assert request.url.path.startswith("/table/v1/foot/49.100000,55.800000;")
        assert request.url.params["annotations"] == "duration,distance"
        # A route exists from museum to park. The reverse direction is blocked.
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "distances": [[0, 300], [None, 0]],
                "durations": [[0, 181], [None, 0]],
            },
        )

    result = preview_client(
        response, {**REQUEST, "wishes": "Музеи с десяти, и бесплатно"}
    )
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["preferences"]["unhandled_requests"] == ["Вход бесплатный"]
    assert plan["routing_source"] == "osrm"
    stops = plan["days"][0]["stops"]
    assert [stop["place_id"] for stop in stops] == ["museum", "park"]
    assert stops[0]["start_time"] == "10:00:00"
    assert stops[1]["walking_from_previous"] == {
        "distance_meters": 300,
        "duration_minutes": 4,
    }
    assert plan["metrics"]["uncertain_hours"] == 2


@pytest.mark.parametrize("status", [500, 200])
def test_osrm_failure_preserves_plan_with_labeled_estimate(
    preview_client: PreviewRequest,
    status: int,
) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "osrm.test"
        return httpx.Response(status, json={"code": "Ok", "durations": [[]]})

    result = preview_client(response, REQUEST)
    assert result.status_code == 200, result.text
    assert result.json()["routing_source"] == "estimate"
    assert result.json()["days"][0]["walking_minutes"] > 0
    assert result.json()["metrics"]["model_ms"] == 0


def test_osrm_no_table_does_not_estimate_an_unreachable_leg(
    preview_client: PreviewRequest,
) -> None:
    result = preview_client(
        lambda _: httpx.Response(400, json={"code": "NoTable"}), REQUEST
    )
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["routing_source"] == "osrm"
    assert plan["metrics"]["scheduled_visits"] == 1


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (503, {"error": "provider details"}, 503),
        (200, {"message": {"content": "{malformed"}}, 502),
        (
            200,
            {"message": {"content": '{"preferred_categories":["hallucinated"]}'}},
            502,
        ),
        (200, {"message": {"content": "{}"}, "done_reason": "length"}, 502),
    ],
)
def test_model_errors_are_reported_through_api_without_leaking_provider_body(
    preview_client: PreviewRequest,
    status: int,
    body: dict[str, Any],
    expected: int,
) -> None:
    result = preview_client(
        lambda _: httpx.Response(status, json=body),
        {**REQUEST, "wishes": "Выбрать музеи"},
    )
    assert result.status_code == expected, result.text
    assert "detail" in result.json()
    assert "provider details" not in result.text


def test_http_client_timeout_becomes_unavailable_model(
    preview_client: PreviewRequest,
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Mocked timeout", request=request)

    result = preview_client(timeout, {**REQUEST, "wishes": "Выбрать музеи"})
    assert result.status_code == 503


@pytest.mark.parametrize(
    "update",
    [
        {"wishes": "Музеи", "preferences": {}},
        {"preferences": {"preferred_categories": ["bad"]}},
        {"preferences": {"day_start": "10:00+03:00"}},
        {"preferences": {"day_end": "08:00"}},
        {"end_date": "2026-12-25"},
    ],
)
def test_invalid_input_is_rejected_before_provider_calls(
    preview_client: PreviewRequest,
    update: dict[str, Any],
) -> None:
    def unexpected_call(_: httpx.Request) -> httpx.Response:
        pytest.fail(
            "Invalid preferences must be rejected without an HTTP provider call"
        )

    result = preview_client(unexpected_call, {**REQUEST, **update})
    assert result.status_code == 422, result.text
