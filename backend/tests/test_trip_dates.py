from datetime import date, timedelta

import pytest

from backend.app.domain.trips import trip_day_count, validate_date_range


@pytest.mark.parametrize("days", [1, 366])
def test_trip_duration_counts_both_dates(days: int) -> None:
    start = date(2028, 1, 1)
    end = start + timedelta(days=days - 1)

    validate_date_range(start, end)

    assert trip_day_count(start, end) == days


@pytest.mark.parametrize("days", [0, 367])
def test_trip_duration_rejects_out_of_range_dates(days: int) -> None:
    start = date(2028, 1, 1)
    end = start + timedelta(days=days - 1)

    with pytest.raises(ValueError):
        validate_date_range(start, end)
