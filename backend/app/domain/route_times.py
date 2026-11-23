from dataclasses import dataclass
from math import asin, ceil, cos, radians, sin, sqrt
from typing import Literal

type Coordinates = tuple[float, float]
type RoutingSource = Literal["osrm", "estimate", "not_needed"]


@dataclass(frozen=True, slots=True)
class WalkingLeg:
    distance_meters: int
    duration_minutes: int


type WalkingMatrix = dict[tuple[str, str], WalkingLeg]


def estimate_walking(points: dict[str, Coordinates]) -> WalkingMatrix:
    matrix = {}
    for origin, (lat1, lon1) in points.items():
        for target, (lat2, lon2) in points.items():
            half_chord = sin(radians(lat2 - lat1) / 2) ** 2 + (
                cos(radians(lat1))
                * cos(radians(lat2))
                * sin(radians(lon2 - lon1) / 2) ** 2
            )
            direct = 12_742_000 * asin(sqrt(min(1, half_chord)))
            # Approximate street detours and a 4.5 km/h walking pace.
            distance = ceil(direct * 1.25)
            matrix[origin, target] = WalkingLeg(distance, ceil(distance / 75))
    return matrix
