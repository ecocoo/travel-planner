import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.application.exceptions import AuthenticationError, ConflictError
from backend.app.application.guest_sessions import resolve_guest
from backend.app.infrastructure.repositories import auth
from backend.app.security.guest_tokens import generate_guest_token, hash_guest_token
from backend.app.security.passwords import dummy_password_hash, password_hasher

SESSION_LIFETIME = timedelta(days=30)


async def resolve_account(connection: AsyncConnection, token: str | None) -> RowMapping:
    account = (
        await auth.find_session(connection, hash_guest_token(token)) if token else None
    )
    if account is None:
        raise AuthenticationError("Valid account token required")
    return account


async def resolve_owner(connection: AsyncConnection, token: str | None) -> UUID:
    if token:
        account = await auth.find_session(connection, hash_guest_token(token))
        if account is not None:
            return account["id"]
    return await resolve_guest(connection, token)


class AuthService:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def register(
        self,
        name: str,
        email: str,
        password: str,
        role: str,
        guest_token: str | None = None,
    ) -> dict:
        password_hash = await asyncio.to_thread(password_hasher.hash, password)
        user = await auth.create_user(
            self.connection,
            {
                "id": uuid4(),
                "name": name,
                "email": email,
                "password_hash": password_hash,
                "role": role,
            },
        )
        if user is None:
            raise ConflictError("Email is already registered")
        return await self._sign_in(user, guest_token)

    async def login(
        self, email: str, password: str, guest_token: str | None = None
    ) -> dict:
        user = await auth.find_by_email(self.connection, email)
        stored_hash = (
            user["password_hash"]
            if user
            else await asyncio.to_thread(dummy_password_hash)
        )
        valid = await asyncio.to_thread(password_hasher.verify, password, stored_hash)
        if user is None or not valid:
            raise AuthenticationError("Invalid email or password")
        return await self._sign_in(user, guest_token)

    async def _sign_in(self, user: RowMapping, guest_token: str | None) -> dict:
        transferred = (
            await self.claim_guest(user["id"], guest_token) if guest_token else 0
        )
        token = generate_guest_token()
        expires_at = datetime.now(UTC) + SESSION_LIFETIME
        await auth.create_session(
            self.connection,
            {
                "id": uuid4(),
                "user_id": user["id"],
                "token_hash": hash_guest_token(token),
                "expires_at": expires_at,
            },
        )
        public_user = {
            key: user[key] for key in ("id", "name", "email", "role", "created_at")
        }
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at,
            "user": public_user,
            "transferred_trips": transferred,
        }

    async def claim_guest(self, user_id: UUID, guest_token: str) -> int:
        guest = await auth.lock_guest(self.connection, hash_guest_token(guest_token))
        if guest is None:
            raise AuthenticationError("Valid guest token required")
        if guest["claimed_by"] == user_id:
            return 0
        if guest["claimed_by"] is not None:
            raise ConflictError("Guest trips have already been transferred")
        return await auth.transfer_trips(self.connection, guest["id"], user_id)

    async def logout(self, session_id: UUID) -> None:
        await auth.delete_session(self.connection, session_id)

    async def enable_guide(self, user_id: UUID) -> RowMapping:
        return await auth.enable_guide(self.connection, user_id)
