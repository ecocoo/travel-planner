from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.auth import AuthService, resolve_account, resolve_owner
from backend.app.application.catalog import CatalogService
from backend.app.application.exceptions import InvalidGuestTokenError
from backend.app.application.generation import RouteGenerationService
from backend.app.application.route_templates import RouteTemplateService
from backend.app.application.trips import TripService
from backend.app.config.config import get_settings
from backend.app.infrastructure.db.database import (
    get_auth_transaction,
    get_connection,
    get_transaction,
)

ReadConnection = Annotated[AsyncConnection, Depends(get_connection, scope="function")]
# FastAPI closes the function-scoped transaction before sending the response.
Transaction = Annotated[AsyncConnection, Depends(get_transaction, scope="function")]
bearer = HTTPBearer(auto_error=False, scheme_name="BearerToken")
read_snapshot = asynccontextmanager(get_connection)


async def get_guest_id(
    connection: Transaction,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    return await resolve_owner(
        connection, credentials.credentials if credentials is not None else None
    )


def get_catalog_service(connection: ReadConnection) -> CatalogService:
    return CatalogService(connection)


def get_trip_service(connection: Transaction) -> TripService:
    return TripService(connection)


def get_route_template_service(connection: ReadConnection) -> RouteTemplateService:
    return RouteTemplateService(connection)


async def get_preview_guest(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    if credentials is None:
        raise InvalidGuestTokenError("Valid guest token required")
    async with read_snapshot() as connection:
        return await resolve_owner(connection, credentials.credentials)


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

RouteTemplates = Annotated[RouteTemplateService, Depends(get_route_template_service)]


AuthTransaction = Annotated[
    AsyncConnection, Depends(get_auth_transaction, scope="function")
]


async def get_account(
    connection: AuthTransaction,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> RowMapping:
    return await resolve_account(
        connection, credentials.credentials if credentials else None
    )


def get_auth_service(connection: AuthTransaction) -> AuthService:
    return AuthService(connection)


Account = Annotated[RowMapping, Depends(get_account)]
Auth = Annotated[AuthService, Depends(get_auth_service)]
