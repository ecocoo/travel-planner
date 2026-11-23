from datetime import date
from typing import Any

import pytest

from backend.app.domain.itinerary import (
    Candidate,
    DayLimits,
    PinnedScheduleError,
    assemble_itinerary,
    insert_visit,
)
from backend.app.domain.opening_hours import WEEKDAYS, find_visit_time
from backend.app.domain.route_times import WalkingLeg, estimate_walking

MONDAY = date(2026, 12, 7)


def candidate(place_id: str, **fields: Any) -> Candidate:
    defaults = {
        "coordinates": None,
        "hours": {},
        "seasonal_months": None,
        "duration": 30,
        "visit_mode": "indoor",
    }
    return Candidate(place_id, place_id, **{**defaults, **fields})


def test_scheduling_waits_for_opening_and_respects_last_admission() -> None:
    hours = {
        "weekly": {day: [{"opens": "10:00", "closes": "18:00"}] for day in WEEKDAYS},
        "last_admission": "17:00",
        "exhibition_clearance_time": "17:45",
    }
    assert find_visit_time(hours, MONDAY, 540, 30, 1140) == (600, "listed_hours")
    assert find_visit_time(hours, MONDAY, 1020, 45, 1140) == (1020, "listed_hours")
    assert find_visit_time(hours, MONDAY, 1021, 20, 1140) is None
    assert find_visit_time(hours, MONDAY, 1020, 46, 1140) is None


@pytest.mark.parametrize(
    "hours,closed_day",
    [
        ({"annual_closed_dates": ["12-07"]}, MONDAY),
        ({"recurring_closures": [{"weekday": "mon", "ordinal": 1}]}, MONDAY),
        (
            {"recurring_closures": [{"weekday": "mon", "ordinal": -1, "months": [12]}]},
            date(2026, 12, 28),
        ),
    ],
)
def test_known_closure_blocks_visits_even_without_weekly_hours(
    hours: dict[str, Any], closed_day: date
) -> None:
    assert find_visit_time(hours, closed_day, 540, 30, 1140) is None


def test_schedule_accounts_for_previous_day_overnight_window() -> None:
    weekly: dict[str, list[dict[str, Any]]] = {day: [] for day in WEEKDAYS}
    weekly["sun"] = [{"opens": "19:00", "closes": "02:00", "closes_next_day": True}]
    hours = {"weekly": weekly, "requires_calendar_resolution": True}
    assert find_visit_time(hours, MONDAY, 30, 45, 120) == (30, "calendar_unresolved")
    assert find_visit_time(hours, MONDAY, 90, 45, 180) is None


def test_optimizer_keeps_pinned_order_and_inserts_before_late_anchor() -> None:
    anchor_a = candidate(
        "a",
        pinned=True,
        preferred_day=1,
        hours={
            "weekly": {day: [{"opens": "17:00", "closes": "18:00"}] for day in WEEKDAYS}
        },
    )
    anchor_b = candidate("b", pinned=True, preferred_day=1)
    extra = candidate("extra")
    matrix = {
        (origin, target): WalkingLeg(100, 5)
        for origin in ("a", "b", "extra")
        for target in ("a", "b", "extra")
    }
    (day,) = assemble_itinerary(
        [MONDAY], [extra], [], [anchor_a, anchor_b], DayLimits(540, 1085, 3, 10), matrix
    )
    assert [stop.candidate.id for stop in day] == [extra.id, anchor_a.id, anchor_b.id]


def test_no_path_and_walking_budget_limit_candidate_insertion() -> None:
    limits = DayLimits(540, 1140, 4, 4)
    a, b = candidate("a"), candidate("b")
    (day,) = assemble_itinerary([MONDAY], [a], [], [], limits, {})
    assert insert_visit(MONDAY, day, b, limits, {}, keep_order=False) is None
    legs = {("a", "b"): WalkingLeg(350, 5), ("b", "a"): WalkingLeg(350, 5)}
    assert insert_visit(MONDAY, day, b, limits, legs, keep_order=False) is None


def test_infeasible_pin_surfaces_conflict() -> None:
    stop = candidate("closed", pinned=True, preferred_day=2, seasonal_months=(1, 2))
    with pytest.raises(PinnedScheduleError, match="day 2"):
        assemble_itinerary(
            [MONDAY, date(2026, 12, 8)],
            [],
            [],
            [stop],
            DayLimits(540, 1140, 4, 90),
            {},
        )


def test_template_uses_original_day_then_moves_visit_closed_on_that_date() -> None:
    sunday = date(2026, 12, 6)
    a = candidate("a", preferred_day=1, hours={"annual_closed_dates": ["12-06"]})
    b = candidate("b", preferred_day=2)
    days = assemble_itinerary(
        [sunday, MONDAY],
        [],
        [a, b],
        [],
        DayLimits(540, 1140, 2, 90),
        {("b", "a"): WalkingLeg(50, 2)},
    )
    assert [[stop.candidate.place_id for stop in day] for day in days] == [
        [],
        ["b", "a"],
    ]


def test_walking_estimate_has_meters_and_ceil_minutes() -> None:
    matrix = estimate_walking({"a": (0, 0), "b": (0, 0.01)})
    assert matrix["a", "b"] == WalkingLeg(1390, 19)
    assert matrix["b", "a"] == matrix["a", "b"]
    assert matrix["a", "a"] == WalkingLeg(0, 0)
