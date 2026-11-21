import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Connection, select

from app.database import get_transaction
from app.tables import guest_sessions

Transaction = Annotated[Connection, Depends(get_transaction, scope="function")]
bearer = HTTPBearer(auto_error=False, scheme_name="GuestToken")


def get_guest_id(
    connection: Transaction,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    if credentials is not None:
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        guest_id = connection.scalar(
            select(guest_sessions.c.id).where(guest_sessions.c.token_hash == digest)
        )
        if guest_id is not None:
            return guest_id
    raise HTTPException(
        status_code=401,
        detail="Valid guest token required",
        headers={"WWW-Authenticate": "Bearer"},
    )


Guest = Annotated[UUID, Depends(get_guest_id)]
