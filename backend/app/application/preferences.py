from datetime import time
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

type PreferenceText = Annotated[str, Field(min_length=1, max_length=200)]


class TravelPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    preferred_categories: list[PreferenceText] = Field(
        default_factory=list, max_length=15
    )
    excluded_categories: list[PreferenceText] = Field(
        default_factory=list, max_length=15
    )
    preferred_places: list[PreferenceText] = Field(default_factory=list, max_length=15)
    excluded_places: list[PreferenceText] = Field(default_factory=list, max_length=15)
    day_start: time = time(9)
    day_end: time = time(19)
    max_stops_per_day: int = Field(default=4, ge=1, le=12)
    max_walking_minutes_per_day: int = Field(default=90, ge=0, le=720)
    unhandled_requests: list[PreferenceText] = Field(
        default_factory=list, max_length=15
    )

    @model_validator(mode="after")
    def validate_preferences(self) -> Self:
        for value in (self.day_start, self.day_end):
            if value.second or value.microsecond or value.tzinfo is not None:
                raise ValueError("Use local HH:MM with minute precision")
        if self.day_start >= self.day_end:
            raise ValueError("day_start must be earlier than day_end")
        if set(self.preferred_categories) & set(self.excluded_categories):
            raise ValueError("A category cannot be both preferred and excluded")
        return self
