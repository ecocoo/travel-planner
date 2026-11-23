from functools import lru_cache

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://travel:travel@localhost:5432/travel"
    ollama_url: HttpUrl = HttpUrl("http://127.0.0.1:11434")
    ollama_model: str = "qwen3.5:4b"
    ollama_timeout_seconds: float = Field(default=45, gt=0, le=180)
    osrm_url: HttpUrl | None = None
    osrm_timeout_seconds: float = Field(default=5, gt=0, le=30)


@lru_cache
def get_settings() -> Settings:
    return Settings()
