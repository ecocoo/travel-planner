from datetime import date, timedelta
from typing import Any, Literal

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
type HoursStatus = Literal["listed_hours", "calendar_unresolved", "unknown"]


def clock_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def closed_on(hours: dict[str, Any], day: date) -> bool:
    if day.strftime("%m-%d") in hours.get("annual_closed_dates", ()):
        return True
    next_week = day + timedelta(days=7) if day <= date.max - timedelta(days=7) else None
    for closure in hours.get("recurring_closures", ()):
        if closure["weekday"] != WEEKDAYS[day.weekday()]:
            continue
        if "months" in closure and day.month not in closure["months"]:
            continue
        ordinal = closure["ordinal"]
        if ordinal in (None, (day.day - 1) // 7 + 1):
            return True
        if ordinal == -1 and (next_week is None or next_week.month != day.month):
            return True
    return False


def find_visit_time(
    hours: dict[str, Any], day: date, earliest: int, duration: int, end_by: int
) -> tuple[int, HoursStatus] | None:
    if earliest + duration > end_by or closed_on(hours, day):
        return None
    weekly = hours.get("weekly")
    if weekly is None:
        return earliest, "unknown"

    status = (
        "calendar_unresolved"
        if hours.get("requires_calendar_resolution")
        else "listed_hours"
    )
    starts = []
    for shift in (-1, 0):
        if shift == -1 and day == date.min:
            continue
        source_day = day + timedelta(days=shift)
        if closed_on(hours, source_day):
            continue
        for window in weekly[WEEKDAYS[source_day.weekday()]]:
            overnight = window.get("closes_next_day", False)
            if shift == -1 and not overnight:
                continue
            opens = clock_minutes(window["opens"])
            closes = clock_minutes(window["closes"]) + 1440 * overnight
            offset = 1440 * shift
            start = max(earliest, opens + offset)

            # Admission and clearance apply to the interval's opening date.
            admission = hours.get("last_admission")
            if admission is not None:
                admission_time = clock_minutes(admission)
                if overnight and admission_time < opens:
                    admission_time += 1440
                if start > admission_time + offset:
                    continue
            clearance = hours.get("exhibition_clearance_time")
            if clearance is not None:
                clearance_time = clock_minutes(clearance)
                if overnight and clearance_time < opens:
                    clearance_time += 1440
                closes = min(closes, clearance_time)
            if start + duration <= min(end_by, closes + offset):
                starts.append(start)
    return (min(starts), status) if starts else None
