from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.schemas.common import ErrorResponse
from backend.app.application.exceptions import (
    ConflictError,
    DatabaseUnavailableError,
    InvalidGuestTokenError,
)
from backend.app.shared.exceptions import ApplicationError
from backend.app.shared.logging_config import get_logger

logger = get_logger(__name__)


async def application_error(request: Request, error: ApplicationError) -> JSONResponse:
    headers = (
        {"WWW-Authenticate": "Bearer"}
        if isinstance(error, InvalidGuestTokenError)
        else None
    )
    return JSONResponse(
        status_code=error.status_code,
        content=ErrorResponse(detail=error.details).model_dump(),
        headers=headers,
    )


async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
    code = getattr(getattr(error, "orig", None), "sqlstate", None)
    if code in {"40001", "40P01", "23505"}:
        return await application_error(
            request,
            ConflictError("Concurrent change. Fetch the latest revision and retry"),
        )
    logger.bind(error_type=type(error).__name__, sqlstate=code).error(
        "Database request failed"
    )
    return await application_error(
        request, DatabaseUnavailableError("Database is unavailable")
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApplicationError, application_error)
    app.add_exception_handler(SQLAlchemyError, database_error)
