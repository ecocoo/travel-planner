from typing import Literal

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class SourceTemplate(BaseModel):
    id: str
    name: str
    status: str
    route_ready: bool
