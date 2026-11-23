from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.catalog import CatalogService
from backend.app.application.exceptions import InvalidGuestTokenError
from backend.app.application.generation import RouteGenerationService
from backend.app.application.guest_sessions import resolve_guest
from backend.app.application.trips import TripService
from backend.app.config.config import get_settings
from backend.app.infrastructure.db.database import get_connection, get_transaction

ReadConnection = Annotated[AsyncConnection, Depends(get_connection, scope="function")]
# FastAPI closes the function-scoped transaction before sending the response.
Transaction = Annotated[AsyncConnection, Depends(get_transaction, scope="function")]
bearer = HTTPBearer(auto_error=False, scheme_name="GuestToken")
read_snapshot = asynccontextmanager(get_connection)


async def get_guest_id(
    connection: Transaction,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    return await resolve_guest(
        connection, credentials.credentials if credentials is not None else None
    )


def get_catalog_service(connection: ReadConnection) -> CatalogService:
    return CatalogService(connection)


def get_trip_service(connection: Transaction) -> TripService:
    return TripService(connection)


async def get_preview_guest(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    if credentials is None:
        raise InvalidGuestTokenError("Valid guest token required")
    async with read_snapshot() as connection:
        return await resolve_guest(connection, credentials.credentials)


async def get_route_generation_service() -> AsyncIterator[RouteGenerationService]:
    async with httpx.AsyncClient(trust_env=False) as client:
        yield RouteGenerationService(read_snapshot, client, get_settings())


Guest = Annotated[UUID, Depends(get_guest_id)]
Catalog = Annotated[CatalogService, Depends(get_catalog_service)]
Trips = Annotated[TripService, Depends(get_trip_service)]
PreviewGuest = Annotated[UUID, Depends(get_preview_guest)]
RouteGeneration = Annotated[
    RouteGenerationService, Depends(get_route_generation_service, scope="function")
]
