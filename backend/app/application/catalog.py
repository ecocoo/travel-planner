from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.exceptions import NotFoundError
from backend.app.domain.catalog import PlaceSearch
from backend.app.infrastructure.repositories import catalog


class CatalogService:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def list_cities(self) -> list[dict[str, Any]]:
        return [dict(city) for city in await catalog.list_cities(self.connection)]

    async def list_categories(self) -> list[dict[str, Any]]:
        return [
            dict(category)
            for category in await catalog.list_categories(self.connection)
        ]

    async def list_places(
        self, filters: PlaceSearch, *, map_only: bool = False
    ) -> dict[str, Any]:
        items, total = await catalog.read_page(
            self.connection, filters, map_only=map_only
        )
        return {
            "items": items,
            "total": total,
            "limit": filters.limit,
            "offset": filters.offset,
        }

    async def get_place(self, place_id: str) -> dict[str, Any]:
        place = await catalog.find_place(self.connection, place_id)
        if place is None:
            raise NotFoundError("Place not found")
        return place
