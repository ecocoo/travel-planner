from datetime import date, datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.api.schemas.catalog import Attribution, PlaceSummary
from backend.app.api.schemas.common import SourceTemplate
from backend.app.domain.itinerary import VisitMode
from backend.app.domain.trips import validate_date_range


class TripInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GuestSession(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TripCreate(TripInput):
    title: str = Field(min_length=1, max_length=200)
    city_id: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        validate_date_range(self.start_date, self.end_date)
        return self


class TripUpdate(TripInput):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    city_id: str | None = Field(default=None, min_length=1, max_length=100)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def reject_null_changes(self) -> Self:
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Changed fields must have non-null values")
        return self


class StopInput(TripInput):
    id: UUID = Field(default_factory=uuid4)
    place_id: str = Field(min_length=1, max_length=200)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    notes: str = Field(default="", max_length=2000)
    pinned: bool | None = None
    visit_mode: VisitMode | None = None


class DayInput(TripInput):
    day_number: int = Field(ge=1, le=366)
    stops: list[StopInput] = Field(default_factory=list, max_length=100)


class ItineraryUpdate(TripInput):
    expected_revision: int = Field(ge=1)
    days: list[DayInput] = Field(max_length=366)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        numbers = [day.day_number for day in self.days]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Day numbers must be unique")
        ids = [stop.id for day in self.days for stop in day.stops]
        if len(ids) != len(set(ids)):
            raise ValueError("Stop IDs must be unique across the itinerary")
        return self


class TripSummary(BaseModel):
    id: UUID
    title: str
    city_id: str
    start_date: date
    end_date: date
    revision: int
    created_at: datetime
    updated_at: datetime
    source_template: SourceTemplate | None = None


class TripStop(BaseModel):
    id: UUID
    position: int
    duration_minutes: int | None
    notes: str
    pinned: bool
    visit_mode: VisitMode | None
    place: PlaceSummary


class TripDay(BaseModel):
    day_number: int
    date: date
    stops: list[TripStop]


class TripDetail(TripSummary):
    days: list[TripDay]
    attribution: Attribution = Field(default_factory=Attribution)
