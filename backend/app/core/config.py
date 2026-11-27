"""Application settings, read from environment variables (see .env.example in the repo root)."""

from functools import lru_cache
from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"  # local | production
    # What the proxy serves (proxy/Caddyfile): ":80" locally. On Railway the backend service sets
    # ENVIRONMENT=production instead (docs/deploy.md).
    site_address: str = ":80"

    # postgresql+asyncpg://user:password@host:5432/dbname. A plain postgresql:// URL, as Railway
    # gives it, is accepted too and gets the asyncpg driver.
    database_url: str = "postgresql+asyncpg://bookcrunch:bookcrunch@localhost:5432/bookcrunch"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url")
    @classmethod
    def use_the_asyncpg_driver(cls, value: str) -> str:
        for plain in ("postgresql://", "postgres://"):
            if value.startswith(plain):
                return "postgresql+asyncpg://" + value.removeprefix(plain)
        return value

    # Uploaded book files (FR-002). A Docker volume in Compose.
    storage_dir: str = "./storage"
    # Largest accepted book file (FR-002). The proxy has the same limit (proxy/Caddyfile).
    max_upload_bytes: int = 50 * 1024 * 1024

    # Rate limits (section 4.2, app.api.rate_limit): login attempts per client address per minute,
    # uploads per user per hour.
    login_rate_limit: int = 10
    upload_rate_limit: int = 20

    # Signs the session JWT (section 7.3). Must be overridden outside local development.
    jwt_secret: str = "change-me-local-only"

    @property
    def is_local(self) -> bool:
        """Local development: plain HTTP on this machine. ENVIRONMENT alone is not trusted,
        because a host set up from .env.example still says "local"; a site address with a domain
        name means the application is reachable by others."""
        address = self.site_address.removeprefix("https://").removeprefix("http://")
        host = address.split(":")[0]
        return self.environment == "local" and host in ("", "localhost", "127.0.0.1")

    @model_validator(mode="after")
    def deployed_application_needs_a_real_secret(self) -> Self:
        """Anyone who knows the secret can sign a session for any user, so outside local
        development the application must not start with the default or a guessable one."""
        weak = len(self.jwt_secret) < MIN_JWT_SECRET_LENGTH or self.jwt_secret.startswith(
            "change-me"
        )
        if weak and not self.is_local:
            raise ValueError(
                f"JWT_SECRET must be a random string of at least {MIN_JWT_SECRET_LENGTH} "
                "characters outside local development (see .env.example)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
