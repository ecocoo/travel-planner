from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.exception_handlers import register_exception_handlers
from backend.app.api.routers import catalog, generation, health, route_templates, trips
from backend.app.infrastructure.db.database import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Travel Planner API", version="0.1.0", lifespan=lifespan)
    app.include_router(catalog.router)
    app.include_router(trips.router)
    app.include_router(generation.router)
    app.include_router(route_templates.router)
    app.include_router(health.router)
    register_exception_handlers(app)
    return app


app = create_app()
