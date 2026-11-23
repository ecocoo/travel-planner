from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from backend.app.api.dependencies import (
    PreviewGuest,
    RouteGeneration,
    get_preview_guest,
)
from backend.app.api.schemas.generation import (
    AdaptRouteInput,
    GenerateRouteInput,
    RebuildRouteInput,
    RoutePreview,
)
from backend.app.application.generation import PreferenceInput

router = APIRouter(
    prefix="/api/v1",
    tags=["route planning"],
    dependencies=[Depends(get_preview_guest)],
)


@router.post("/route-plans")
async def generate_route(
    data: GenerateRouteInput, service: RouteGeneration
) -> RoutePreview:
    return RoutePreview.model_validate(
        await service.generate(
            data.city_id,
            data.start_date,
            data.end_date,
            PreferenceInput(data.wishes, data.preferences),
        )
    )


@router.post("/route-templates/{template_id}/adapt")
async def adapt_route(
    template_id: Annotated[str, Path(min_length=1, max_length=200)],
    data: AdaptRouteInput,
    service: RouteGeneration,
) -> RoutePreview:
    return RoutePreview.model_validate(
        await service.generate(
            None,
            data.start_date,
            data.end_date,
            PreferenceInput(data.wishes, data.preferences),
            template_id=template_id,
        )
    )


@router.post("/trips/{trip_id}/itinerary/rebuild")
async def rebuild_itinerary(
    trip_id: UUID,
    data: RebuildRouteInput,
    guest_id: PreviewGuest,
    service: RouteGeneration,
) -> RoutePreview:
    return RoutePreview.model_validate(
        await service.rebuild(
            trip_id,
            guest_id,
            data.expected_revision,
            PreferenceInput(data.wishes, data.preferences),
        )
    )
