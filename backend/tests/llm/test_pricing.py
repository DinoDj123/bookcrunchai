"""Cost of a call from the price table (FR-013, TC-018). No database needed."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.core.config import Settings
from app.llm.pricing import ModelPrice, cost_usd, get_prices, load_prices

PRICE = ModelPrice(input_per_million_usd=Decimal("2.00"), output_per_million_usd=Decimal("10.00"))


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "expected"),
    [
        (1_000_000, 0, "2.000000"),
        (0, 1_000_000, "10.000000"),
        (12_000, 800, "0.032000"),  # 0.024 for input + 0.008 for output
        (1, 0, "0.000002"),
        (0, 0, "0.000000"),
    ],
)
def test_cost_of_known_token_counts(input_tokens: int, output_tokens: int, expected: str) -> None:
    cost = cost_usd(PRICE, input_tokens, output_tokens)

    assert cost == Decimal(expected)
    assert isinstance(cost, Decimal)


def test_cost_is_rounded_to_six_decimals_like_the_database_column() -> None:
    cheap = ModelPrice(Decimal("0.10"), Decimal("0.50"))

    # 7 input tokens cost 0.0000007 USD, which rounds up to the smallest storable amount.
    assert cost_usd(cheap, 7, 0) == Decimal("0.000001")
    assert cost_usd(cheap, 4, 0) == Decimal("0.000000")


def test_prices_are_read_from_a_file_as_decimals(tmp_path: Path) -> None:
    prices_file = tmp_path / "prices.json"
    prices_file.write_text(
        '{"some-model": {"input_per_million_usd": "0.10", "output_per_million_usd": "0.50"}}',
        encoding="utf-8",
    )

    assert load_prices(prices_file) == {"some-model": ModelPrice(Decimal("0.10"), Decimal("0.50"))}


def test_the_default_models_have_a_price() -> None:
    """A model without a price cannot be used; the defaults must work out of the box."""
    settings = Settings(_env_file=None)

    assert settings.llm_model_fast in get_prices()
    assert settings.llm_model_strong in get_prices()
