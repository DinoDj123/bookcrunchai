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
