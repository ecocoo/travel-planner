from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid4

from backend.app.domain.opening_hours import HoursStatus, find_visit_time
from backend.app.domain.route_times import Coordinates, WalkingLeg, WalkingMatrix

MAX_PLAN_DAYS = 14
MAX_ROUTING_POINTS = 80
type VisitMode = Literal["indoor", "outdoor", "mixed", "exterior", "scheduled"]


@dataclass(frozen=True, slots=True)
class Candidate:
    place_id: str
    name: str
    coordinates: Coordinates | None
    hours: dict[str, Any]
    seasonal_months: tuple[int, ...] | None
    duration: int
    visit_mode: VisitMode
    notes: str = ""
    pinned: bool = False
    id: UUID = field(default_factory=uuid4)
    preferred_day: int | None = None
    stored_duration: int | None = None
    needs_booking: bool = False


@dataclass(frozen=True, slots=True)
class DayLimits:
    start: int
    end: int
    max_stops: int
    max_walking: int


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    candidate: Candidate
    start: int
    leg: WalkingLeg | None
    hours_status: HoursStatus


class PinnedScheduleError(ValueError):
    def __init__(self, day_number: int) -> None:
        super().__init__(f"Pinned visits do not fit on day {day_number}")


def schedule_day(
    day: date,
    candidates: list[Candidate],
    limits: DayLimits,
    matrix: WalkingMatrix,
) -> list[ScheduledStop] | None:
    if len(candidates) > limits.max_stops:
        return None
    stops = []
    minute, walking = limits.start, 0
    previous = None
    for candidate in candidates:
        if candidate.seasonal_months is not None and day.month not in (
            candidate.seasonal_months
        ):
            return None
        leg = None
        if previous is not None:
            leg = (
                WalkingLeg(0, 0)
                if previous.place_id == candidate.place_id
                else matrix.get((previous.place_id, candidate.place_id))
            )
            if leg is None:
                return None
            minute += leg.duration_minutes
            walking += leg.duration_minutes
            if walking > limits.max_walking:
                return None
        slot = find_visit_time(
            candidate.hours, day, minute, candidate.duration, limits.end
        )
        if slot is None:
            return None
        start, hours_status = slot
        stops.append(ScheduledStop(candidate, start, leg, hours_status))
        minute, previous = start + candidate.duration, candidate
    return stops


def insert_visit(
    day: date,
    stops: list[ScheduledStop],
    candidate: Candidate,
    limits: DayLimits,
    matrix: WalkingMatrix,
    *,
    keep_order: bool,
) -> list[ScheduledStop] | None:
    current = [stop.candidate for stop in stops]
    # Recheck the whole day for each insertion: adding a visit can push a later
    # pinned stop past closing or take the day over its walking budget.
    positions = [len(current)] if keep_order else range(len(current) + 1)
    best = None
    best_score = None
    for index in positions:
        trial = schedule_day(
            day, [*current[:index], candidate, *current[index:]], limits, matrix
        )
        if trial is None:
            continue
        score = (
            trial[-1].start + trial[-1].candidate.duration,
            sum(stop.leg.duration_minutes for stop in trial if stop.leg is not None),
        )
        if best_score is None or score < best_score:
            best, best_score = trial, score
    return best


def assemble_itinerary(
    dates: list[date],
    catalog: list[Candidate],
    template: list[Candidate],
    pinned: list[Candidate],
    limits: DayLimits,
    matrix: WalkingMatrix,
) -> list[list[ScheduledStop]]:
    days = []
    for index, travel_date in enumerate(dates, start=1):
        anchors = [stop for stop in pinned if stop.preferred_day == index]
        scheduled = schedule_day(travel_date, anchors, limits, matrix)
        if scheduled is None:
            raise PinnedScheduleError(index)
        days.append(scheduled)
    used = {stop.place_id for stop in pinned}

    # Fill editorial days before redistributing their remaining visits. This
    # prevents an early day from consuming a visit that fits its original day.
    for redistribute in (False, True):
        for index, travel_date in enumerate(dates, start=1):
            for candidate in template:
                if candidate.place_id in used:
                    continue
                if not redistribute and candidate.preferred_day != index:
                    continue
                if len(days[index - 1]) >= limits.max_stops:
                    break
                trial = insert_visit(
                    travel_date,
                    days[index - 1],
                    candidate,
                    limits,
                    matrix,
                    keep_order=True,
                )
                if trial is not None:
                    days[index - 1] = trial
                    used.add(candidate.place_id)
    for index, travel_date in enumerate(dates):
        for candidate in catalog:
            if len(days[index]) >= limits.max_stops:
                break
            if candidate.place_id in used:
                continue
            trial = insert_visit(
                travel_date,
                days[index],
                candidate,
                limits,
                matrix,
                keep_order=bool(template),
            )
            if trial is not None:
                days[index] = trial
                used.add(candidate.place_id)
    return days
