from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RouteTemplateSearch:
    city_id: str | None = None
    status: str | None = None
    route_ready: bool | None = None
    limit: int = 50
    offset: int = 0


def template_counts(template: dict[str, Any]) -> dict[str, int]:
    return {
        "days_count": max((day["day_number"] for day in template["days"]), default=0),
        "stops_count": sum(len(day["stops"]) for day in template["days"]),
    }


def copy_template_days(template: dict[str, Any]) -> list[dict[str, Any]]:
    days = template["days"]
    numbers = [day["day_number"] for day in days]
    if not numbers or len(set(numbers)) != len(numbers):
        raise ValueError("Template must have distinct day numbers")
    if any(type(number) is not int or not 1 <= number <= 366 for number in numbers):
        raise ValueError("Template days must be between 1 and 366")
    copied = []
    for day in sorted(days, key=lambda item: item["day_number"]):
        orders = [stop["order"] for stop in day["stops"]]
        if len(set(orders)) != len(orders) or any(
            type(order) is not int or order < 1 for order in orders
        ):
            raise ValueError("Template stop orders must be positive and distinct")
        copied.append(
            {
                "day_number": day["day_number"],
                "stops": [
                    {
                        "id": uuid4(),
                        "place_id": stop["place_id"],
                        "duration_minutes": stop.get("duration_minutes"),
                        "visit_mode": stop.get("visit_mode"),
                        "pinned": False,
                        "notes": "",
                    }
                    for stop in sorted(day["stops"], key=lambda item: item["order"])
                ],
            }
        )
    return copied
