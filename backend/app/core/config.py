"""Application settings, read from environment variables (see .env.example in the repo root)."""

from decimal import Decimal
from functools import lru_cache
from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32
DEFAULT_LLM_BASE_URL = "http://localhost:11434/v1"  # Ollama on this machine


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

    # Open-weight models behind an OpenAI-compatible API (app.llm, specification 5.3): Ollama
    # locally (the default), a hosted provider on the demo host. The key is only needed by
    # hosted providers.
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str | None = None
    # A smaller, faster model for chapter notes, a stronger one for the book summary and questions
    # (specification 9). Names as the server knows them. Every model used needs a price in
    # config/prices.json (0 for a model that costs nothing).
    llm_model_fast: str = "gemma3:4b"
    llm_model_strong: str = "gemma3:12b"
    # No new LLM call for a book that has already cost this much (FR-017). Temporary value, to be
    # set after the first cost measurements.
    book_cost_limit_usd: Decimal = Decimal("2.00")
    # Parallel LLM requests in one worker process, over all its jobs.
    llm_max_concurrency: int = 5
    # One attempt of one LLM request; a call is tried up to 3 times.
    llm_timeout_seconds: float = 180

    # Rate limits (section 4.2, app.api.rate_limit): login attempts per client address per minute,
    # uploads per user per hour.
    login_rate_limit: int = 10
    register_rate_limit: int = 5  # registrations per client address per minute
    upload_rate_limit: int = 20
    summary_rate_limit: int = 20  # summary jobs started per user per hour

    # Signs the session JWT (section 7.3). Must be overridden outside local development.
    jwt_secret: str = "change-me-local-only"

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def empty_llm_base_url_means_the_default(cls, value: object) -> object:
        """.env.example leaves LLM_BASE_URL empty: then Ollama on this machine is used."""
        return value or DEFAULT_LLM_BASE_URL

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
