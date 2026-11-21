from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, Select, and_, func, or_, select

from app.database import get_connection
from app.schemas import (
    Category,
    CitySummary,
    MapPage,
    PlaceDetail,
    PlaceFilters,
    PlacePage,
)
from app.tables import categories, cities, place_categories, places

router = APIRouter(prefix="/api/v1", tags=["catalog"])
Database = Annotated[Connection, Depends(get_connection)]
Filters = Annotated[PlaceFilters, Query()]


@router.get("/cities", response_model=list[CitySummary])
def list_cities(connection: Database):
    statement = (
        select(
            cities.c.id,
            cities.c.name,
            cities.c.timezone,
            func.count(places.c.id).label("places_count"),
        )
        .select_from(cities.outerjoin(places))
        .group_by(cities.c.id)
        .order_by(cities.c.name, cities.c.id)
    )
    return connection.execute(statement).mappings().all()


@router.get("/categories", response_model=list[Category])
def list_categories(connection: Database):
    return (
        connection.execute(
            select(categories).order_by(categories.c.name, categories.c.id)
        )
        .mappings()
        .all()
    )


def filtered_places(filters: PlaceFilters, *, map_only: bool = False) -> Select:
    statement = select(places.c.id, places.c.payload)
    if filters.city_id is not None:
        statement = statement.where(places.c.city_id == filters.city_id)
    if filters.category_id is not None:
        matching_category = (
            select(place_categories.c.place_id)
            .where(
                place_categories.c.place_id == places.c.id,
                place_categories.c.category_id == filters.category_id,
            )
            .exists()
        )
        statement = statement.where(matching_category)
    if filters.entity_type is not None:
        statement = statement.where(places.c.entity_type == filters.entity_type)
    if filters.route_ready is not None:
        statement = statement.where(places.c.route_ready == filters.route_ready)
    if filters.q is not None:
        # Unicode collation also handles Cyrillic in databases initialized with C locale.
        searchable = [
            places.c.name,
            places.c.payload["description"].astext,
            places.c.payload["address"].astext,
        ]
        statement = statement.where(
            or_(
                *(
                    column.collate("pg_unicode_fast").icontains(
                        filters.q, autoescape=True
                    )
                    for column in searchable
                )
            )
        )
    usable_coordinates = and_(
        places.c.latitude.is_not(None),
        places.c.longitude.is_not(None),
        places.c.payload["location"]["status"].astext.is_distinct_from("conflicting"),
    )
    if map_only or filters.min_lat is not None:
        statement = statement.where(usable_coordinates)
    if filters.has_coordinates is not None:
        statement = statement.where(usable_coordinates == filters.has_coordinates)
    if filters.min_lat is not None:
        statement = statement.where(
            places.c.latitude.between(filters.min_lat, filters.max_lat),
            places.c.longitude.between(filters.min_lon, filters.max_lon),
        )
    return statement


def read_page(connection: Connection, statement: Select, filters: PlaceFilters) -> dict:
    total = connection.scalar(select(func.count()).select_from(statement.subquery()))
    records = connection.execute(
        statement.order_by(places.c.name, places.c.id)
        .limit(filters.limit)
        .offset(filters.offset)
    ).mappings()
    return {
        "items": [record["payload"] for record in records],
        "total": total,
        "limit": filters.limit,
        "offset": filters.offset,
    }


@router.get("/places", response_model=PlacePage)
def list_places(filters: Filters, connection: Database):
    return read_page(connection, filtered_places(filters), filters)


@router.get("/places/map", response_model=MapPage)
def list_map_markers(filters: Filters, connection: Database):
    """Paginated markers with usable source coordinates, including attribution."""
    return read_page(connection, filtered_places(filters, map_only=True), filters)


@router.get(
    "/places/{place_id}", response_model=PlaceDetail, response_model_exclude_unset=True
)
def get_place(place_id: str, connection: Database):
    payload = connection.scalar(select(places.c.payload).where(places.c.id == place_id))
    if payload is None:
        raise HTTPException(status_code=404, detail="Place not found")
    return payload
