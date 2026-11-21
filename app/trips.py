import hashlib
import secrets
from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import Connection, delete, func, insert, select, update

from app.guest_auth import Guest, Transaction
from app.schemas import Page
from app.tables import cities, guest_sessions, places, trip_stops, trips
from app.trip_schemas import (
    GuestSession,
    ItineraryUpdate,
    TripCreate,
    TripDetail,
    TripSummary,
    TripUpdate,
    validate_date_range,
)

router = APIRouter(prefix="/api/v1", tags=["trips"])


@router.post("/guest-sessions", response_model=GuestSession, status_code=201)
def create_guest_session(connection: Transaction, response: Response):
    token = secrets.token_urlsafe(32)
    connection.execute(
        insert(guest_sessions).values(
            id=uuid4(),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token}


def owned_trip(connection: Connection, trip_id: UUID, guest_id: UUID):
    trip = (
        connection.execute(
            select(trips)
            .where(trips.c.id == trip_id, trips.c.owner_id == guest_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


def check_revision(trip, expected: int) -> None:
    if trip["revision"] != expected:
        raise HTTPException(
            status_code=409, detail="Trip changed. Fetch the latest revision and retry"
        )


def require_city(connection: Connection, city_id: str) -> None:
    if connection.scalar(select(cities.c.id).where(cities.c.id == city_id)) is None:
        raise HTTPException(status_code=422, detail="Unknown city_id")


def trip_detail(connection: Connection, trip) -> dict:
    days = [
        {
            "day_number": index + 1,
            "date": trip["start_date"] + timedelta(days=index),
            "stops": [],
        }
        for index in range((trip["end_date"] - trip["start_date"]).days + 1)
    ]
    stops = connection.execute(
        select(trip_stops, places.c.payload.label("place"))
        .join(places, trip_stops.c.place_id == places.c.id)
        .where(trip_stops.c.trip_id == trip["id"])
        .order_by(trip_stops.c.day_number, trip_stops.c.position)
    ).mappings()
    for stop in stops:
        days[stop["day_number"] - 1]["stops"].append(dict(stop))
    return {**trip, "days": days}


@router.post("/trips", response_model=TripDetail, status_code=201)
def create_trip(data: TripCreate, guest_id: Guest, connection: Transaction):
    require_city(connection, data.city_id)
    trip = (
        connection.execute(
            insert(trips)
            .values(
                id=uuid4(),
                owner_id=guest_id,
                **data.model_dump(),
            )
            .returning(trips)
        )
        .mappings()
        .one()
    )
    return trip_detail(connection, trip)


@router.get("/trips", response_model=Page[TripSummary])
def list_trips(
    guest_id: Guest,
    connection: Transaction,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    statement = select(trips).where(trips.c.owner_id == guest_id)
    total = connection.scalar(select(func.count()).select_from(statement.subquery()))
    records = (
        connection.execute(
            statement.order_by(trips.c.created_at.desc(), trips.c.id)
            .limit(limit)
            .offset(offset)
        )
        .mappings()
        .all()
    )
    return {"items": records, "total": total, "limit": limit, "offset": offset}


@router.get("/trips/{trip_id}", response_model=TripDetail)
def get_trip(trip_id: UUID, guest_id: Guest, connection: Transaction):
    return trip_detail(connection, owned_trip(connection, trip_id, guest_id))


@router.patch("/trips/{trip_id}", response_model=TripDetail)
def update_trip(
    trip_id: UUID, data: TripUpdate, guest_id: Guest, connection: Transaction
):
    trip = owned_trip(connection, trip_id, guest_id)
    check_revision(trip, data.expected_revision)
    changes = data.model_dump(exclude_unset=True, exclude={"expected_revision"})
    merged = {**trip, **changes}
    try:
        validate_date_range(merged["start_date"], merged["end_date"])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    require_city(connection, merged["city_id"])
    day_count = (merged["end_date"] - merged["start_date"]).days + 1
    invalid_stop = connection.scalar(
        select(trip_stops.c.id)
        .join(places, trip_stops.c.place_id == places.c.id)
        .where(
            trip_stops.c.trip_id == trip_id,
            (trip_stops.c.day_number > day_count)
            | (places.c.city_id != merged["city_id"]),
        )
        .limit(1)
    )
    if invalid_stop is not None:
        raise HTTPException(
            status_code=409,
            detail="Existing stops conflict with the new dates or city. Update the itinerary first",
        )
    updated = (
        connection.execute(
            update(trips)
            .where(trips.c.id == trip_id)
            .values(**changes, revision=trip["revision"] + 1, updated_at=func.now())
            .returning(trips)
        )
        .mappings()
        .one()
    )
    return trip_detail(connection, updated)


@router.put("/trips/{trip_id}/itinerary", response_model=TripDetail)
def replace_itinerary(
    trip_id: UUID, data: ItineraryUpdate, guest_id: Guest, connection: Transaction
):
    trip = owned_trip(connection, trip_id, guest_id)
    check_revision(trip, data.expected_revision)
    day_count = (trip["end_date"] - trip["start_date"]).days + 1
    if any(day.day_number > day_count for day in data.days):
        raise HTTPException(status_code=422, detail="Day number exceeds trip duration")
    rows = [
        {
            "trip_id": trip_id,
            "day_number": day.day_number,
            "position": index,
            **stop.model_dump(),
        }
        for day in data.days
        for index, stop in enumerate(day.stops, start=1)
    ]
    place_ids = {row["place_id"] for row in rows}
    found = set(
        connection.scalars(
            select(places.c.id).where(
                places.c.id.in_(place_ids),
                places.c.city_id == trip["city_id"],
            )
        )
    )
    if found != place_ids:
        raise HTTPException(
            status_code=422,
            detail="Every stop must reference an existing place in the trip city",
        )
    stop_ids = [row["id"] for row in rows]
    foreign_stop = connection.scalar(
        select(trip_stops.c.id)
        .where(
            trip_stops.c.id.in_(stop_ids),
            trip_stops.c.trip_id != trip_id,
        )
        .limit(1)
    )
    if foreign_stop is not None:
        raise HTTPException(status_code=409, detail="Stop ID is already in use")
    connection.execute(delete(trip_stops).where(trip_stops.c.trip_id == trip_id))
    if rows:
        connection.execute(insert(trip_stops), rows)
    updated = (
        connection.execute(
            update(trips)
            .where(trips.c.id == trip_id)
            .values(
                revision=trip["revision"] + 1,
                updated_at=func.now(),
            )
            .returning(trips)
        )
        .mappings()
        .one()
    )
    return trip_detail(connection, updated)


@router.delete("/trips/{trip_id}", status_code=204)
def delete_trip(
    trip_id: UUID,
    guest_id: Guest,
    connection: Transaction,
    expected_revision: Annotated[int, Query(ge=1)],
):
    trip = owned_trip(connection, trip_id, guest_id)
    check_revision(trip, expected_revision)
    connection.execute(delete(trips).where(trips.c.id == trip_id))
    return Response(status_code=204)
