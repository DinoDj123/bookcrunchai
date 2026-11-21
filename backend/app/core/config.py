"""Application settings, read from environment variables (see .env.example in the repo root)."""

from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"  # local | production

    # postgresql+asyncpg://user:password@host:5432/dbname
    database_url: str = "postgresql+asyncpg://bookcrunch:bookcrunch@localhost:5432/bookcrunch"
    redis_url: str = "redis://localhost:6379/0"

    # Uploaded book files (FR-002). A Docker volume in Compose.
    storage_dir: str = "./storage"
    # Largest accepted book file (FR-002). The proxy has the same limit (Caddyfile).
    max_upload_bytes: int = 50 * 1024 * 1024

    # Signs the session JWT (section 7.3). Must be overridden outside local development.
    jwt_secret: str = "change-me-local-only"

    @model_validator(mode="after")
    def deployed_application_needs_a_real_secret(self) -> Self:
        """Anyone who knows the secret can sign a session for any user, so outside local
        development the application must not start with the default or a guessable one."""
        weak = len(self.jwt_secret) < MIN_JWT_SECRET_LENGTH or self.jwt_secret.startswith(
            "change-me"
        )
        if self.environment != "local" and weak:
            raise ValueError(
                f"JWT_SECRET must be a random string of at least {MIN_JWT_SECRET_LENGTH} "
                "characters outside local development (see .env.example)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
