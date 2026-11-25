from fastapi import APIRouter, Response

from backend.app.api.dependencies import Account, Auth
from backend.app.api.schemas.auth import (
    AuthSession,
    ClaimGuestInput,
    ClaimGuestResult,
    LoginInput,
    RegisterInput,
    UserProfile,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=AuthSession, status_code=201)
async def register(data: RegisterInput, service: Auth, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await service.register(
        data.name,
        str(data.email),
        data.password.get_secret_value(),
        data.role,
        data.guest_token,
    )


@router.post("/login", response_model=AuthSession)
async def login(data: LoginInput, service: Auth, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await service.login(
        str(data.email), data.password.get_secret_value(), data.guest_token
    )


@router.get("/me", response_model=UserProfile)
async def me(account: Account, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return account


@router.post("/logout", status_code=204)
async def logout(account: Account, service: Auth):
    await service.logout(account["session_id"])
    return Response(status_code=204)


@router.post("/me/guide", response_model=UserProfile)
async def enable_guide(account: Account, service: Auth):
    return await service.enable_guide(account["id"])


@router.post("/claim-guest-trips", response_model=ClaimGuestResult)
async def claim_guest(data: ClaimGuestInput, account: Account, service: Auth):
    count = await service.claim_guest(account["id"], data.guest_token)
    return ClaimGuestResult(transferred_trips=count)
