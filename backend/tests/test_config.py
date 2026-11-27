"""Settings refuse to run the deployed application with a weak JWT secret."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_local_development_may_use_the_default_secret() -> None:
    assert Settings(_env_file=None, environment="local").jwt_secret


@pytest.mark.parametrize("secret", ["change-me-local-only", "change-me", "short"])
def test_production_needs_a_real_secret(secret: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", jwt_secret=secret)


def test_production_accepts_a_long_random_secret() -> None:
    settings = Settings(_env_file=None, environment="production", jwt_secret="k" * 48)

    assert settings.jwt_secret == "k" * 48


def test_upload_limit_is_50_megabytes_by_default() -> None:
    assert Settings(_env_file=None).max_upload_bytes == 50 * 1024 * 1024


@pytest.mark.parametrize("site_address", ["bookcrunch.example.com", "https://bookcrunch.me"])
def test_site_with_a_domain_needs_a_real_secret_even_if_marked_local(site_address: str) -> None:
    """A demo host set up from .env.example still says ENVIRONMENT=local, but has a domain."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="local", site_address=site_address)


@pytest.mark.parametrize("site_address", [":80", ":8080", "localhost", "localhost:8080"])
def test_local_addresses_count_as_local_development(site_address: str) -> None:
    assert Settings(_env_file=None, site_address=site_address).is_local is True


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+asyncpg"])
def test_database_url_always_uses_the_asyncpg_driver(scheme: str) -> None:
    """Railway's DATABASE_URL starts with postgresql://, which SQLAlchemy opens with psycopg2."""
    settings = Settings(_env_file=None, database_url=f"{scheme}://u:p@db.railway.internal:5432/d")

    assert settings.database_url == "postgresql+asyncpg://u:p@db.railway.internal:5432/d"


def test_production_is_never_local() -> None:
    settings = Settings(_env_file=None, environment="production", jwt_secret="k" * 48)

    assert settings.is_local is False
