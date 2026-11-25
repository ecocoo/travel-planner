from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.schemas.common import ErrorResponse
from backend.app.application.exceptions import (
    AuthenticationError,
    ConflictError,
    DatabaseUnavailableError,
)
from backend.app.shared.exceptions import ApplicationError
from backend.app.shared.logging_config import get_logger

logger = get_logger(__name__)


async def application_error(request: Request, error: ApplicationError) -> JSONResponse:
    headers = (
        {"WWW-Authenticate": "Bearer"}
        if isinstance(error, AuthenticationError)
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


async def validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    if request.url.path.startswith("/api/v1/auth/"):
        # Validation responses must not echo passwords or guest tokens.
        errors = [
            {key: value for key, value in item.items() if key != "input"}
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422, content={"detail": jsonable_encoder(errors)}
        )
    return await request_validation_exception_handler(request, error)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(ApplicationError, application_error)
    app.add_exception_handler(SQLAlchemyError, database_error)
