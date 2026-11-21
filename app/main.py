import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.catalog import router as catalog_router
from app.database import get_engine
from app.trips import router as trips_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    get_engine().dispose()


app = FastAPI(title="Travel Planner API", version="0.1.0", lifespan=lifespan)
app.include_router(catalog_router)
app.include_router(trips_router)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
    code = getattr(getattr(error, "orig", None), "sqlstate", None)
    if code in {"40001", "40P01", "23505"}:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Concurrent change. Fetch the latest revision and retry"
            },
        )
    logger.error("Database request failed", exc_info=error)
    return JSONResponse(status_code=503, content={"detail": "Database is unavailable"})


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1 FROM places LIMIT 1"))
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        raise HTTPException(
            status_code=503, detail="Database is unavailable or migrations are pending"
        ) from None
    return {"status": "ok"}
