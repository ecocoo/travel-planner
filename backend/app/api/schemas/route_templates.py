from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.api.schemas.catalog import Attribution, PlaceSummary
from backend.app.api.schemas.common import SourceTemplate
from backend.app.api.schemas.trips import TripInput
from backend.app.domain.itinerary import VisitMode
from backend.app.domain.trips import validate_date_range


class RouteTemplateFilters(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    city_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, min_length=1, max_length=100)
    route_ready: bool | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class RouteTemplateSummary(SourceTemplate):
    city_id: str
    description: str
    origin: str
    days_count: int
    stops_count: int


class RouteTemplateStop(BaseModel):
    order: int
    place_id: str
    visit_mode: VisitMode | None = None
    duration_minutes: int | None = None
    duration_basis: str | None = None
    place: PlaceSummary | None


class RouteTemplateDay(BaseModel):
    day_number: int
    stops: list[RouteTemplateStop]


class RouteTemplateDetail(RouteTemplateSummary):
    days: list[RouteTemplateDay]
    walking_minutes: int | None
    distance_meters: int | None
    validation_notes: list[str]
    source_urls: list[str]
    attribution: Attribution = Field(default_factory=Attribution)


class RouteTemplateCopy(TripInput):
    start_date: date
    end_date: date | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_dates(self) -> Self:
        if self.end_date is not None:
            validate_date_range(self.start_date, self.end_date)
        return self
