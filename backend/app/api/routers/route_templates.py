from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.app.api.dependencies import Guest, RouteTemplates, Trips
from backend.app.api.schemas.catalog import Page
from backend.app.api.schemas.route_templates import (
    RouteTemplateCopy,
    RouteTemplateDetail,
    RouteTemplateFilters,
    RouteTemplateSummary,
)
from backend.app.api.schemas.trips import TripDetail
from backend.app.domain.route_templates import RouteTemplateSearch

router = APIRouter(prefix="/api/v1/route-templates", tags=["route templates"])
TemplateId = Annotated[str, Path(min_length=1, max_length=200)]
Filters = Annotated[RouteTemplateFilters, Query()]


@router.get("", response_model=Page[RouteTemplateSummary])
async def list_templates(
    filters: Filters,
    service: RouteTemplates,
) -> Page[RouteTemplateSummary]:
    return Page[RouteTemplateSummary].model_validate(
        await service.list_templates(RouteTemplateSearch(**filters.model_dump()))
    )


@router.get("/{template_id}", response_model=RouteTemplateDetail)
async def get_template(
    template_id: TemplateId,
    service: RouteTemplates,
) -> RouteTemplateDetail:
    return RouteTemplateDetail.model_validate(await service.get_template(template_id))


@router.post("/{template_id}/copy", response_model=TripDetail, status_code=201)
async def copy_template(
    template_id: TemplateId,
    data: RouteTemplateCopy,
    guest_id: Guest,
    service: Trips,
) -> TripDetail:
    return TripDetail.model_validate(
        await service.create_from_template(
            guest_id,
            template_id,
            data.start_date,
            data.end_date,
            data.title,
        )
    )
