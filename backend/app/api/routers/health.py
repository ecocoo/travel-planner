from fastapi import APIRouter

from backend.app.api.schemas.common import HealthResponse
from backend.app.application.health import check_readiness

router = APIRouter(prefix="/health")


@router.get("/live")
async def live() -> HealthResponse:
    return HealthResponse()


@router.get("/ready")
async def ready() -> HealthResponse:
    await check_readiness()
    return HealthResponse()
