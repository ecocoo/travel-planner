from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.dependencies import Catalog
from backend.app.api.schemas.catalog import (
    Category,
    CitySummary,
    MapPage,
    PlaceDetail,
    PlaceFilters,
    PlacePage,
)
from backend.app.domain.catalog import PlaceSearch

router = APIRouter(prefix="/api/v1", tags=["catalog"])
Filters = Annotated[PlaceFilters, Query()]


@router.get("/cities", response_model=list[CitySummary])
async def list_cities(service: Catalog) -> list[CitySummary]:
    return [CitySummary.model_validate(city) for city in await service.list_cities()]


@router.get("/categories", response_model=list[Category])
async def list_categories(service: Catalog) -> list[Category]:
    return [
        Category.model_validate(category)
        for category in await service.list_categories()
    ]


@router.get("/places", response_model=PlacePage)
async def list_places(filters: Filters, service: Catalog) -> PlacePage:
    return PlacePage.model_validate(
        await service.list_places(PlaceSearch(**filters.model_dump()))
    )


@router.get("/places/map", response_model=MapPage)
async def list_map_markers(filters: Filters, service: Catalog) -> MapPage:
    """Paginated markers with usable source coordinates, including attribution."""
    return MapPage.model_validate(
        await service.list_places(PlaceSearch(**filters.model_dump()), map_only=True)
    )


@router.get(
    "/places/{place_id}", response_model=PlaceDetail, response_model_exclude_unset=True
)
async def get_place(place_id: str, service: Catalog) -> PlaceDetail:
    return PlaceDetail.model_validate(await service.get_place(place_id))
