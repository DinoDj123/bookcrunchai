"""Application settings, read from environment variables (see .env.example in the repo root)."""

from decimal import Decimal
from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"  # local | production
    # What the proxy serves (Caddyfile): ":80" locally, the domain name on the demo host.
    site_address: str = ":80"

    # postgresql+asyncpg://user:password@host:5432/dbname
    database_url: str = "postgresql+asyncpg://bookcrunch:bookcrunch@localhost:5432/bookcrunch"
    redis_url: str = "redis://localhost:6379/0"

    # Uploaded book files (FR-002). A Docker volume in Compose.
    storage_dir: str = "./storage"
    # Largest accepted book file (FR-002). The proxy has the same limit (Caddyfile).
    max_upload_bytes: int = 50 * 1024 * 1024

    # Claude API (app.llm). Without a key the application runs, but any LLM call fails.
    anthropic_api_key: str | None = None
    # A cheaper model for chapter notes, a stronger one for the book summary and questions
    # (specification 9.1). Every model used needs a price in config/prices.json.
    claude_model_fast: str = "claude-haiku-5-5"
    claude_model_strong: str = "claude-sonnet-5-5"
    # No new LLM call for a book that has already cost this much (FR-017). Temporary value, to be
    # set after the first cost measurements.
    book_cost_limit_usd: Decimal = Decimal("2.00")
    # Parallel LLM requests in one worker process, over all its jobs.
    llm_max_concurrency: int = 5
    # One attempt of one LLM request; a call is tried up to 3 times.
    llm_timeout_seconds: float = 180

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
