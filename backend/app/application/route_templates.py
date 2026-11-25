from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.exceptions import NotFoundError
from backend.app.domain.route_templates import RouteTemplateSearch, template_counts
from backend.app.infrastructure.repositories import route_templates


class RouteTemplateService:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def list_templates(self, filters: RouteTemplateSearch) -> dict[str, Any]:
        records, total = await route_templates.read_page(self.connection, filters)
        return {
            "items": [{**record, **template_counts(record)} for record in records],
            "total": total,
            "limit": filters.limit,
            "offset": filters.offset,
        }

    async def get_template(self, template_id: str) -> dict[str, Any]:
        template = await route_templates.find_template(self.connection, template_id)
        if template is None:
            raise NotFoundError("Route template not found")
        place_ids = {
            stop["place_id"] for day in template["days"] for stop in day["stops"]
        }
        places = await route_templates.read_places(self.connection, place_ids)
        days = [
            {
                **day,
                "stops": [
                    {**stop, "place": places.get(stop["place_id"])}
                    for stop in sorted(day["stops"], key=lambda stop: stop["order"])
                ],
            }
            for day in sorted(template["days"], key=lambda day: day["day_number"])
        ]
        return {**template, **template_counts(template), "days": days}
