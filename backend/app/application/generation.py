from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, time, timedelta
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
)
from backend.app.application.preferences import TravelPreferences
from backend.app.config.config import Settings
from backend.app.domain.itinerary import (
    MAX_PLAN_DAYS,
    MAX_ROUTING_POINTS,
    Candidate,
    DayLimits,
    PinnedScheduleError,
    ScheduledStop,
    assemble_itinerary,
)
from backend.app.domain.route_times import (
    RoutingSource,
    WalkingMatrix,
    estimate_walking,
)
from backend.app.domain.trips import trip_day_count
from backend.app.infrastructure import ollama, osrm
from backend.app.infrastructure.repositories import catalog, route_templates, trips
from backend.app.shared.exceptions import InvalidModelResponseError
from backend.app.shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PreferenceInput:
    wishes: str | None
    preferences: TravelPreferences | None


@dataclass(frozen=True, slots=True)
class PlanSource:
    city_id: str
    timezone: str
    start: date
    end: date
    places: list[dict[str, Any]]
    categories: dict[str, str]
    template: dict[str, Any] | None = None
    pinned: Sequence[RowMapping] = ()
    trip_id: UUID | None = None
    revision: int | None = None


def local_time(minutes: int) -> time:
    return time(*divmod(minutes, 60))


def matches_place(term: str, place: dict[str, Any]) -> bool:
    return term == place["id"] or (
        term.casefold().replace("ё", "е") in place["name"].casefold().replace("ё", "е")
    )


def make_candidate(
    place: dict[str, Any],
    stop: dict[str, Any] | None = None,
) -> Candidate:
    choice = stop or {}
    location = place["location"]
    coordinates = None
    if location and location["status"] != "conflicting":
        coordinates = location["latitude"], location["longitude"]
    mode = choice.get("visit_mode") or place["visit_mode"]
    if choice.get("pinned"):
        # Preserve the UUID, notes and persisted duration even when it is null.
        identity = {"id": choice["id"], "pinned": True, "notes": choice["notes"]}
    else:
        identity = {}
    admission = place["admission"]
    outdoor_view = mode == "exterior" and place["visit_mode"] != "exterior"
    return Candidate(
        place_id=place["id"],
        name=place["name"],
        coordinates=coordinates,
        hours={} if outdoor_view else place["opening_hours"],
        seasonal_months=place["seasonality"]["months"],
        duration=choice.get("duration_minutes")
        or place["visit_duration"]["minutes"]
        or 60,
        visit_mode=mode,
        preferred_day=choice.get("day_number"),
        stored_duration=choice.get("duration_minutes"),
        needs_booking=not outdoor_view
        and (
            mode == "scheduled"
            or bool(admission["booking_required"])
            or bool(admission["timed_entry"])
        ),
        **identity,
    )


def ranked_candidates(
    source: PlanSource, preferences: TravelPreferences
) -> tuple[list[Candidate], list[Candidate], list[str]]:
    preferred = set(preferences.preferred_categories)
    eligible = {
        place["id"]: place
        for place in source.places
        if not excluded_place(place, preferences)
    }
    template = {}
    template_ids = []
    if source.template is not None:
        for day in source.template["days"]:
            for stop in day["stops"]:
                place_id = stop["place_id"]
                template_ids.append(place_id)
                if place_id in eligible and place_id not in template:
                    template[place_id] = make_candidate(
                        eligible[place_id], {**stop, "day_number": day["day_number"]}
                    )
    template_set = set(template_ids)
    ranked = sorted(
        (place for id_, place in eligible.items() if id_ not in template_set),
        key=lambda place: (
            -sum(matches_place(term, place) for term in preferences.preferred_places),
            -len(preferred.intersection(place["categories"])),
            not place["route_ready"],
            place["opening_hours"]["weekly"] is None,
            place["id"],
        ),
    )
    candidates = [make_candidate(place) for place in ranked]
    return candidates, list(template.values()), template_ids


def excluded_place(place: dict[str, Any], preferences: TravelPreferences) -> bool:
    return bool(
        set(preferences.excluded_categories).intersection(place["categories"])
        or any(matches_place(term, place) for term in preferences.excluded_places)
    )


def serialize_day(day: date, number: int, stops: list[ScheduledStop]) -> dict[str, Any]:
    visits = []
    spent_walking = distance = spent_visiting = 0
    for stop in stops:
        candidate = stop.candidate
        if stop.leg:
            spent_walking += stop.leg.duration_minutes
            distance += stop.leg.distance_meters
        spent_visiting += candidate.duration
        visits.append(
            {
                "id": candidate.id,
                "place_id": candidate.place_id,
                "name": candidate.name,
                "pinned": candidate.pinned,
                "duration_minutes": (
                    candidate.stored_duration
                    if candidate.pinned
                    else candidate.duration
                ),
                "planned_duration_minutes": candidate.duration,
                "visit_mode": candidate.visit_mode,
                "notes": candidate.notes,
                "start_time": local_time(stop.start),
                "end_time": local_time(stop.start + candidate.duration),
                "hours_status": stop.hours_status,
                "needs_booking": candidate.needs_booking,
                "walking_from_previous": stop.leg,
            }
        )
    # Include waiting for openings in the day span, but count it separately.
    span = (
        stops[-1].start + stops[-1].candidate.duration - stops[0].start if stops else 0
    )
    return {
        "day_number": number,
        "date": day,
        "stops": visits,
        "walking_minutes": spent_walking,
        "distance_meters": distance,
        "visit_minutes": spent_visiting,
        "waiting_minutes": span - spent_walking - spent_visiting,
    }


class RouteGenerationService:
    def __init__(
        self,
        connect: Callable[[], AbstractAsyncContextManager[AsyncConnection]],
        client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.connect = connect
        self.client = client
        self.settings = settings

    async def _preferences(
        self, source: PlanSource, data: PreferenceInput
    ) -> TravelPreferences:
        if data.wishes is None:
            preferences = data.preferences or TravelPreferences()
        else:
            preferences = await ollama.extract_preferences(
                self.client,
                str(self.settings.ollama_url),
                self.settings.ollama_model,
                self.settings.ollama_timeout_seconds,
                TravelPreferences,
                data.wishes,
                source.city_id,
                source.categories,
            )
        unknown = (
            set(preferences.preferred_categories) | set(preferences.excluded_categories)
        ) - source.categories.keys()
        if unknown:
            message = "Preferences contain an unknown category ID"
            if data.wishes is not None:
                raise InvalidModelResponseError(message)
            raise InvalidRequestError(message)
        return preferences

    async def _read_source(
        self,
        connection: AsyncConnection,
        city_id: str,
        start: date,
        end: date,
        **options: Any,
    ) -> PlanSource:
        if not 1 <= trip_day_count(start, end) <= MAX_PLAN_DAYS:
            raise InvalidRequestError(
                f"Route planning supports 1 to {MAX_PLAN_DAYS} days"
            )
        timezone = await catalog.find_city_timezone(connection, city_id)
        if timezone is None:
            raise InvalidRequestError("Unknown city_id")
        places = await catalog.list_city_places(connection, city_id)
        categories = {
            row["id"]: row["name"] for row in await catalog.list_categories(connection)
        }
        return PlanSource(city_id, timezone, start, end, places, categories, **options)

    async def generate(
        self,
        city_id: str | None,
        start: date,
        end: date,
        data: PreferenceInput,
        *,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        async with self.connect() as connection:
            template = None
            if template_id is not None:
                template = await route_templates.find_template(connection, template_id)
                if template is None:
                    raise NotFoundError("Route template not found")
                city_id = template["city_id"]
            if city_id is None:
                raise InvalidRequestError("A city is required")
            source = await self._read_source(
                connection, city_id, start, end, template=template
            )
        return await self._plan(source, data, started)

    async def rebuild(
        self,
        trip_id: UUID,
        guest_id: UUID,
        revision: int,
        data: PreferenceInput,
    ) -> dict[str, Any]:
        started = perf_counter()
        # The read snapshot is released before Ollama or OSRM can block. The
        # existing itinerary PUT checks the revision again when the plan is saved.
        async with self.connect() as connection:
            trip = await trips.find_owned(connection, trip_id, guest_id, lock=False)
            if trip is None:
                raise NotFoundError("Trip not found")
            if trip["revision"] != revision:
                raise ConflictError("Trip changed. Fetch the latest revision and retry")
            pinned = [
                row
                for row in await trips.list_stops(connection, trip_id)
                if row["pinned"]
            ]
            source = await self._read_source(
                connection,
                trip["city_id"],
                trip["start_date"],
                trip["end_date"],
                pinned=pinned,
                trip_id=trip_id,
                revision=revision,
            )
        return await self._plan(source, data, started)

    async def _walking(
        self, candidates: list[Candidate]
    ) -> tuple[WalkingMatrix, RoutingSource]:
        points = {
            stop.place_id: stop.coordinates
            for stop in candidates
            if stop.coordinates is not None
        }
        if len(points) < 2:
            return estimate_walking(points), "not_needed"
        if self.settings.osrm_url is not None:
            matrix = await osrm.read_walking_matrix(
                self.client,
                str(self.settings.osrm_url),
                self.settings.osrm_timeout_seconds,
                points,
            )
            if matrix is not None:
                return matrix, "osrm"
        return estimate_walking(points), "estimate"

    async def _plan(
        self, source: PlanSource, data: PreferenceInput, started: float
    ) -> dict[str, Any]:
        model_start = perf_counter()
        preferences = await self._preferences(source, data)
        model_ms = round((perf_counter() - model_start) * 1000, 2) if data.wishes else 0
        candidates, template, template_ids = ranked_candidates(source, preferences)
        pinned = [make_candidate(row["place"], dict(row)) for row in source.pinned]
        pinned_ids = {stop.place_id for stop in pinned}
        if len(pinned_ids) > MAX_ROUTING_POINTS:
            raise ConflictError("Too many pinned places for one planning request")

        # Reserve matrix capacity for pinned and template stops before adding
        # the top-ranked catalog candidates. Missing coordinates are retained
        # for pinned visits, which can still be scheduled alone on a day.
        remaining = MAX_ROUTING_POINTS - len(pinned_ids)
        template = [
            stop
            for stop in template
            if stop.coordinates is not None and stop.place_id not in pinned_ids
        ]
        template = template[:remaining]
        candidates = [
            stop
            for stop in candidates
            if stop.coordinates is not None and stop.place_id not in pinned_ids
        ][: remaining - len(template)]
        routing_start = perf_counter()
        matrix, routing_source = await self._walking([*pinned, *template, *candidates])
        routing_ms = round((perf_counter() - routing_start) * 1000, 2)
        limits = DayLimits(
            preferences.day_start.hour * 60 + preferences.day_start.minute,
            preferences.day_end.hour * 60 + preferences.day_end.minute,
            preferences.max_stops_per_day,
            preferences.max_walking_minutes_per_day,
        )
        dates = [
            source.start + timedelta(days=index)
            for index in range(trip_day_count(source.start, source.end))
        ]
        try:
            schedules = assemble_itinerary(
                dates, candidates, template, pinned, limits, matrix
            )
        except PinnedScheduleError as error:
            raise ConflictError(str(error)) from error
        days = [
            serialize_day(day, number, stops)
            for number, (day, stops) in enumerate(zip(dates, schedules, strict=True), 1)
        ]
        visits = [visit for day in days for visit in day["stops"]]
        scheduled_ids = {visit["place_id"] for visit in visits}
        scheduled_places = [
            place for place in source.places if place["id"] in scheduled_ids
        ]
        warnings = {
            "empty_days": [day["day_number"] for day in days if not day["stops"]],
            "omitted_template_places": [
                id_ for id_ in dict.fromkeys(template_ids) if id_ not in scheduled_ids
            ],
            "unmatched_preferred_places": [
                term
                for term in preferences.preferred_places
                if not any(matches_place(term, place) for place in scheduled_places)
            ],
            "unmatched_preferred_categories": [
                category
                for category in preferences.preferred_categories
                if not any(
                    category in place["categories"] for place in scheduled_places
                )
            ],
            "pinned_excluded_places": list(
                dict.fromkeys(
                    row["place_id"]
                    for row in source.pinned
                    if excluded_place(row["place"], preferences)
                )
            ),
        }
        metrics = {
            "total_ms": round((perf_counter() - started) * 1000, 2),
            "model_ms": model_ms,
            "routing_ms": routing_ms,
            "scheduled_visits": len(visits),
            "pinned_visits": len(pinned),
            "uncertain_hours": sum(v["hours_status"] != "listed_hours" for v in visits),
            "booking_checks": sum(v["needs_booking"] for v in visits),
            "nonempty_days": sum(bool(day["stops"]) for day in days),
        }
        logger.bind(routing_source=routing_source, **metrics).info(
            "Route plan generated"
        )
        return {
            "city_id": source.city_id,
            "timezone": source.timezone,
            "trip_id": source.trip_id,
            "expected_revision": source.revision,
            "preferences": preferences,
            "routing_source": routing_source,
            "template": source.template,
            "days": days,
            "warnings": warnings,
            "metrics": metrics,
        }
