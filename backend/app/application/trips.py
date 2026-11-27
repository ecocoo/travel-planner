from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
)
from backend.app.domain.route_templates import copy_template_days
from backend.app.domain.trips import trip_day_count, validate_date_range
from backend.app.infrastructure.repositories import catalog, route_templates, trips


class TripService:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def _owned_trip(self, trip_id: UUID, guest_id: UUID) -> RowMapping:
        trip = await trips.find_owned(self.connection, trip_id, guest_id)
        if trip is None:
            raise NotFoundError("Trip not found")
        return trip

    @staticmethod
    def _check_revision(trip: RowMapping, expected: int) -> None:
        if trip["revision"] != expected:
            raise ConflictError("Trip changed. Fetch the latest revision and retry")

    async def _require_city(self, city_id: str) -> None:
        if not await catalog.city_exists(self.connection, city_id):
            raise InvalidRequestError("Unknown city_id")

    async def _require_places(self, city_id: str, place_ids: set[str]) -> None:
        found = await trips.find_places_in_city(self.connection, place_ids, city_id)
        if found != place_ids:
            raise InvalidRequestError(
                "Every stop must reference an existing place in the trip city"
            )

    async def _trip_detail(self, trip: RowMapping) -> dict[str, Any]:
        days = [
            {
                "day_number": index + 1,
                "date": trip["start_date"] + timedelta(days=index),
                "stops": [],
            }
            for index in range(trip_day_count(trip["start_date"], trip["end_date"]))
        ]
        for stop in await trips.list_stops(self.connection, trip["id"]):
            days[stop["day_number"] - 1]["stops"].append(dict(stop))
        return {**trip, "days": days}

    async def create_trip(self, guest_id: UUID, data: dict[str, Any]) -> dict[str, Any]:
        await self._require_city(data["city_id"])
        trip = await trips.create(self.connection, uuid4(), guest_id, data)
        return await self._trip_detail(trip)

    async def list_trips(
        self, guest_id: UUID, limit: int, offset: int
    ) -> dict[str, Any]:
        records, total = await trips.list_owned(
            self.connection, guest_id, limit, offset
        )
        return {"items": records, "total": total, "limit": limit, "offset": offset}

    async def get_trip(self, trip_id: UUID, guest_id: UUID) -> dict[str, Any]:
        return await self._trip_detail(await self._owned_trip(trip_id, guest_id))

    async def update_trip(
        self,
        trip_id: UUID,
        guest_id: UUID,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        trip = await self._owned_trip(trip_id, guest_id)
        self._check_revision(trip, expected_revision)
        merged = {**trip, **changes}
        start, end = merged["start_date"], merged["end_date"]
        try:
            validate_date_range(start, end)
        except ValueError as error:
            raise InvalidRequestError(str(error)) from error
        await self._require_city(merged["city_id"])
        if await trips.has_incompatible_stops(
            self.connection, trip_id, trip_day_count(start, end), merged["city_id"]
        ):
            raise ConflictError(
                "Existing stops conflict with the new dates or city. "
                "Update the itinerary first"
            )
        updated = await trips.update_trip(
            self.connection, trip_id, trip["revision"], changes
        )
        return await self._trip_detail(updated)

    async def replace_itinerary(
        self,
        trip_id: UUID,
        guest_id: UUID,
        expected_revision: int,
        days: list[dict[str, Any]],
    ) -> dict[str, Any]:
        trip = await self._owned_trip(trip_id, guest_id)
        self._check_revision(trip, expected_revision)
        await self._replace_stops(trip, days)
        updated = await trips.update_trip(
            self.connection, trip_id, trip["revision"], {}
        )
        return await self._trip_detail(updated)

    async def _replace_stops(
        self,
        trip: RowMapping,
        days: list[dict[str, Any]],
    ) -> None:
        trip_id = trip["id"]
        day_count = trip_day_count(trip["start_date"], trip["end_date"])
        if any(day["day_number"] > day_count for day in days):
            raise InvalidRequestError("Day number exceeds trip duration")
        rows = [
            {
                "trip_id": trip_id,
                "day_number": day["day_number"],
                "position": position,
                **stop,
            }
            for day in days
            for position, stop in enumerate(day["stops"], start=1)
        ]
        await self._require_places(trip["city_id"], {row["place_id"] for row in rows})
        if await trips.has_foreign_stop(
            self.connection, trip_id, [row["id"] for row in rows]
        ):
            raise ConflictError("Stop ID is already in use")
        unspecified = [
            row["id"]
            for row in rows
            if row.get("pinned") is None or "visit_mode" not in row
        ]
        previous = (
            await trips.read_stop_preferences(self.connection, trip_id, unspecified)
            if unspecified
            else {}
        )
        for row in rows:
            old = previous.get(row["id"])
            # Older clients omit these fields when editing an existing stop.
            if row.get("pinned") is None:
                row["pinned"] = old["pinned"] if old is not None else False
            if "visit_mode" not in row:
                row["visit_mode"] = (
                    old["visit_mode"]
                    if old is not None and old["place_id"] == row["place_id"]
                    else None
                )
        await trips.replace_stops(self.connection, trip_id, rows)

    async def edit_stop(
        self,
        trip_id: UUID,
        stop_id: UUID,
        guest_id: UUID,
        expected_revision: int,
        *,
        place_id: str | None,
    ) -> dict[str, Any]:
        trip = await self._owned_trip(trip_id, guest_id)
        self._check_revision(trip, expected_revision)
        day_stops = await trips.list_stops_for_same_day(
            self.connection, trip_id, stop_id
        )
        rows = [dict(row) for row in day_stops]
        stop = next((row for row in rows if row["id"] == stop_id), None)
        if stop is None:
            raise NotFoundError("Stop not found")
        if stop["pinned"]:
            raise ConflictError("Unpin the stop before editing or removing it")

        if place_id is None:
            rows.remove(stop)
            for position, row in enumerate(rows, start=1):
                row["position"] = position
        elif place_id != stop["place_id"]:
            await self._require_places(trip["city_id"], {place_id})
            stop.update(place_id=place_id, visit_mode=None)

        await trips.replace_stops(
            self.connection, trip_id, rows, day_number=stop["day_number"]
        )
        updated = await trips.update_trip(
            self.connection, trip_id, trip["revision"], {}
        )
        return await self._trip_detail(updated)

    async def create_from_template(
        self,
        guest_id: UUID,
        template_id: str,
        start_date: date,
        end_date: date | None,
        title: str | None,
    ) -> dict[str, Any]:
        template = await route_templates.find_template(self.connection, template_id)
        if template is None:
            raise NotFoundError("Route template not found")
        try:
            days = copy_template_days(template)
            required_days = max(day["day_number"] for day in days)
            finish = end_date or start_date + timedelta(days=required_days - 1)
            validate_date_range(start_date, finish)
        except (ValueError, OverflowError) as error:
            raise InvalidRequestError(str(error)) from error
        if trip_day_count(start_date, finish) < required_days:
            raise InvalidRequestError("Trip dates must include all template days")
        name = (title if title is not None else template["name"]).strip()
        if not 1 <= len(name) <= 200:
            raise InvalidRequestError(
                "Provide a trip title between 1 and 200 characters"
            )
        await self._require_city(template["city_id"])
        trip = await trips.create(
            self.connection,
            uuid4(),
            guest_id,
            {
                "title": name,
                "city_id": template["city_id"],
                "start_date": start_date,
                "end_date": finish,
                "source_template": {
                    key: template[key]
                    for key in ("id", "name", "status", "route_ready")
                },
            },
        )
        await self._replace_stops(trip, days)
        return await self._trip_detail(trip)

    async def delete_trip(
        self, trip_id: UUID, guest_id: UUID, expected_revision: int
    ) -> None:
        trip = await self._owned_trip(trip_id, guest_id)
        self._check_revision(trip, expected_revision)
        await trips.remove(self.connection, trip_id)
