from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from backend.app.api.dependencies import Guest, Transaction, Trips
from backend.app.api.schemas.catalog import Page
from backend.app.api.schemas.trips import (
    GuestSession,
    ItineraryUpdate,
    TripCreate,
    TripDetail,
    TripSummary,
    TripUpdate,
)
from backend.app.application.guest_sessions import create_guest_session as issue_token

router = APIRouter(prefix="/api/v1", tags=["trips"])


@router.post("/guest-sessions", response_model=GuestSession, status_code=201)
async def create_guest_session(
    connection: Transaction, response: Response
) -> GuestSession:
    token = await issue_token(connection)
    response.headers["Cache-Control"] = "no-store"
    return GuestSession(access_token=token)


@router.post("/trips", response_model=TripDetail, status_code=201)
async def create_trip(data: TripCreate, guest_id: Guest, service: Trips) -> TripDetail:
    return TripDetail.model_validate(
        await service.create_trip(guest_id, data.model_dump())
    )


@router.get("/trips", response_model=Page[TripSummary])
async def list_trips(
    guest_id: Guest,
    service: Trips,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TripSummary]:
    return Page[TripSummary].model_validate(
        await service.list_trips(guest_id, limit, offset)
    )


@router.get("/trips/{trip_id}", response_model=TripDetail)
async def get_trip(trip_id: UUID, guest_id: Guest, service: Trips) -> TripDetail:
    return TripDetail.model_validate(await service.get_trip(trip_id, guest_id))


@router.patch("/trips/{trip_id}", response_model=TripDetail)
async def update_trip(
    trip_id: UUID, data: TripUpdate, guest_id: Guest, service: Trips
) -> TripDetail:
    changes = data.model_dump(exclude_unset=True, exclude={"expected_revision"})
    return TripDetail.model_validate(
        await service.update_trip(trip_id, guest_id, data.expected_revision, changes)
    )


@router.put("/trips/{trip_id}/itinerary", response_model=TripDetail)
async def replace_itinerary(
    trip_id: UUID, data: ItineraryUpdate, guest_id: Guest, service: Trips
) -> TripDetail:
    days = data.model_dump()["days"]
    # Null explicitly clears the visit mode. Omitting it preserves existing data.
    for day, input_day in zip(days, data.days, strict=True):
        for stop, input_stop in zip(day["stops"], input_day.stops, strict=True):
            if "visit_mode" not in input_stop.model_fields_set:
                del stop["visit_mode"]
    return TripDetail.model_validate(
        await service.replace_itinerary(trip_id, guest_id, data.expected_revision, days)
    )


@router.delete("/trips/{trip_id}", status_code=204)
async def delete_trip(
    trip_id: UUID,
    guest_id: Guest,
    service: Trips,
    expected_revision: Annotated[int, Query(ge=1)],
) -> Response:
    await service.delete_trip(trip_id, guest_id, expected_revision)
    return Response(status_code=204)
