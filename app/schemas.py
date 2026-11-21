from datetime import date
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlaceFilters(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    city_id: str | None = Field(default=None, min_length=1, max_length=100)
    category_id: str | None = Field(default=None, min_length=1, max_length=100)
    q: str | None = Field(default=None, min_length=1, max_length=200)
    entity_type: str | None = Field(default=None, min_length=1, max_length=100)
    route_ready: bool | None = None
    has_coordinates: bool | None = Field(
        default=None,
        description="Coordinates are present and their source status is not conflicting",
    )
    min_lat: float | None = Field(default=None, ge=-90, le=90)
    max_lat: float | None = Field(default=None, ge=-90, le=90)
    min_lon: float | None = Field(default=None, ge=-180, le=180)
    max_lon: float | None = Field(default=None, ge=-180, le=180)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        bounds = (self.min_lat, self.max_lat, self.min_lon, self.max_lon)
        if all(value is None for value in bounds):
            return self
        if any(value is None for value in bounds):
            raise ValueError("Provide all four bounding box coordinates")
        if self.min_lat > self.max_lat or self.min_lon > self.max_lon:
            raise ValueError("Minimum coordinates must not exceed maximum coordinates")
        if self.has_coordinates is False:
            raise ValueError("Bounding box requires has_coordinates=true or omitted")
        return self


class CitySummary(BaseModel):
    id: str
    name: str
    timezone: str
    places_count: int


class Category(BaseModel):
    id: str
    name: str


class Location(BaseModel):
    latitude: float
    longitude: float
    kind: str
    entrance_verified: bool
    status: str


class PlaceSummary(BaseModel):
    id: str
    city_id: str
    name: str
    description: str
    categories: list[str]
    entity_type: str
    address: str | None
    location: Location | None
    record_status: str
    route_ready: bool
    unresolved_fields: list[str]


class PlaceDetail(PlaceSummary):
    model_config = ConfigDict(extra="allow")

    description_basis: str | None = None
    visit_mode: str
    area: str
    parent_place_id: str | None
    opening_hours: dict[str, Any]
    visit_duration: dict[str, Any]
    admission: dict[str, Any]
    seasonality: dict[str, Any]
    accessibility: dict[str, Any]
    official_url: str | None
    official_url_status: str
    detail_url: str | None
    sources: list[dict[str, Any]]
    checked_on: date


class MapMarker(BaseModel):
    id: str
    city_id: str
    name: str
    categories: list[str]
    entity_type: str
    location: Location
    route_ready: bool


class Attribution(BaseModel):
    text: str = "© OpenStreetMap contributors"
    url: str = "https://www.openstreetmap.org/copyright"
    license: str = "ODbL-1.0"
    license_url: str = "https://opendatacommons.org/licenses/odbl/1-0/"


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class PlacePage(Page[PlaceSummary]):
    attribution: Attribution = Field(default_factory=Attribution)


class MapPage(Page[MapMarker]):
    attribution: Attribution = Field(default_factory=Attribution)
