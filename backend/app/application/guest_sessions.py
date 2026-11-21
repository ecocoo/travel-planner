from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.exceptions import InvalidGuestTokenError
from backend.app.infrastructure.repositories import guest_sessions
from backend.app.security.guest_tokens import generate_guest_token, hash_guest_token


async def create_guest_session(connection: AsyncConnection) -> str:
    token = generate_guest_token()
    await guest_sessions.create(connection, uuid4(), hash_guest_token(token))
    return token


async def resolve_guest(connection: AsyncConnection, token: str | None) -> UUID:
    if token is not None:
        guest_id = await guest_sessions.find_id(connection, hash_guest_token(token))
        if guest_id is not None:
            return guest_id
    raise InvalidGuestTokenError("Valid guest token required")
