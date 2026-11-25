from datetime import date, time
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from backend.app.api.schemas.catalog import Attribution
from backend.app.api.schemas.common import SourceTemplate
from backend.app.api.schemas.trips import StopInput, TripInput
from backend.app.application.preferences import TravelPreferences
from backend.app.domain.itinerary import MAX_PLAN_DAYS, VisitMode
from backend.app.domain.opening_hours import HoursStatus
from backend.app.domain.route_times import RoutingSource, WalkingLeg
from backend.app.domain.trips import trip_day_count


class PreviewInput(TripInput):
    wishes: str | None = Field(default=None, min_length=1, max_length=2000)
    preferences: TravelPreferences | None = None

    @model_validator(mode="after")
    def check_preferences(self) -> Self:
        if self.wishes is not None and self.preferences is not None:
            raise ValueError("Provide either wishes or preferences")
        return self


class AdaptRouteInput(PreviewInput):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def check_dates(self) -> Self:
        if not 1 <= trip_day_count(self.start_date, self.end_date) <= MAX_PLAN_DAYS:
            raise ValueError(f"Route planning supports 1 to {MAX_PLAN_DAYS} days")
        return self


class GenerateRouteInput(AdaptRouteInput):
    city_id: str = Field(min_length=1, max_length=100)


class RebuildRouteInput(PreviewInput):
    expected_revision: int = Field(ge=1)


class PlannedStop(StopInput):
    id: UUID
    name: str
    pinned: bool
    planned_duration_minutes: int
    start_time: time
    end_time: time
    hours_status: HoursStatus
    visit_mode: VisitMode
    needs_booking: bool
    walking_from_previous: WalkingLeg | None


class PlannedDay(BaseModel):
    day_number: int
    date: date
    stops: list[PlannedStop]
    walking_minutes: int
    distance_meters: int
    visit_minutes: int
    waiting_minutes: int


class PlanWarnings(BaseModel):
    empty_days: list[int]
    omitted_template_places: list[str]
    unmatched_preferred_places: list[str]
    unmatched_preferred_categories: list[str]
    pinned_excluded_places: list[str]


class PlanMetrics(BaseModel):
    total_ms: float
    model_ms: float
    routing_ms: float
    scheduled_visits: int
    pinned_visits: int
    uncertain_hours: int
    booking_checks: int
    nonempty_days: int


class RoutePreview(BaseModel):
    city_id: str
    timezone: str
    trip_id: UUID | None
    expected_revision: int | None
    preferences: TravelPreferences
    routing_source: RoutingSource
    template: SourceTemplate | None
    days: list[PlannedDay]
    warnings: PlanWarnings
    metrics: PlanMetrics
    attribution: Attribution = Field(default_factory=Attribution)
