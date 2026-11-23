from asyncio import timeout
from math import ceil, isfinite
from typing import Any

import httpx

from backend.app.domain.route_times import Coordinates, WalkingLeg, WalkingMatrix
from backend.app.shared.logging_config import get_logger

logger = get_logger(__name__)


def parse_table(body: dict[str, Any], ids: list[str]) -> WalkingMatrix:
    if body["code"] != "Ok":
        raise ValueError("OSRM table did not succeed")
    matrix = {}
    durations, distances = body["durations"], body["distances"]
    for origin, duration_row, distance_row in zip(
        ids, durations, distances, strict=True
    ):
        for target, seconds, meters in zip(
            ids, duration_row, distance_row, strict=True
        ):
            if seconds is None and meters is None:
                # OSRM explicitly found no path for this directed pair.
                continue
            if any(
                type(value) not in (int, float) or not isfinite(value) or value < 0
                for value in (seconds, meters)
            ):
                raise ValueError("Invalid OSRM distance or duration")
            matrix[origin, target] = WalkingLeg(ceil(meters), ceil(seconds / 60))
    return matrix


async def read_walking_matrix(
    client: httpx.AsyncClient,
    url: str,
    timeout_seconds: float,
    points: dict[str, Coordinates],
) -> WalkingMatrix | None:
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points.values())
    try:
        async with timeout(timeout_seconds):
            response = await client.get(
                f"{url.rstrip('/')}/table/v1/foot/{coordinates}",
                params={"annotations": "duration,distance"},
                timeout=timeout_seconds,
            )
            # NoTable is an explicit absence of paths, including on HTTP 400.
            body = response.json()
            if isinstance(body, dict) and body.get("code") == "NoTable":
                return {}
            response.raise_for_status()
        if not isinstance(body, dict):
            raise ValueError("Invalid OSRM table response")
        return parse_table(body, list(points))
    except (
        httpx.HTTPError,
        TimeoutError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
    ) as error:
        logger.bind(provider="osrm", error_type=type(error).__name__).warning(
            "Walking routes unavailable"
        )
        return None
