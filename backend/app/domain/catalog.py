from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlaceSearch:
    city_id: str | None
    category_id: str | None
    q: str | None
    entity_type: str | None
    route_ready: bool | None
    has_coordinates: bool | None
    min_lat: float | None
    max_lat: float | None
    min_lon: float | None
    max_lon: float | None
    limit: int
    offset: int
