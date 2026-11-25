from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class AuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(AuthInput):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)
    guest_token: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class RegisterInput(LoginInput):
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=8, max_length=128)
    role: Literal["traveler", "guide"] = "traveler"

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class UserProfile(BaseModel):
    id: UUID
    name: str
    email: str
    role: Literal["traveler", "guide"]
    created_at: datetime


class AuthSession(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserProfile
    transferred_trips: int


class ClaimGuestInput(AuthInput):
    guest_token: str = Field(min_length=1, max_length=200)


class ClaimGuestResult(BaseModel):
    transferred_trips: int
