from datetime import date


def trip_day_count(start: date, end: date) -> int:
    return (end - start).days + 1


def validate_date_range(start: date, end: date) -> None:
    if not 1 <= trip_day_count(start, end) <= 366:
        raise ValueError("Trip must last between 1 and 366 days, including both dates")
