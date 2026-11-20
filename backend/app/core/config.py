"""Application settings, read from environment variables (see .env.example in the repo root)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"  # local | production

    # postgresql+asyncpg://user:password@host:5432/dbname
    database_url: str = "postgresql+asyncpg://bookcrunch:bookcrunch@localhost:5432/bookcrunch"
    redis_url: str = "redis://localhost:6379/0"

    # Uploaded book files (FR-002). A Docker volume in Compose.
    storage_dir: str = "./storage"

    # Signs the session JWT (section 7.3). Must be overridden outside local development.
    jwt_secret: str = "change-me-local-only"


@lru_cache
def get_settings() -> Settings:
    return Settings()
