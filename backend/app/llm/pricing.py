"""Price table and cost calculation (FR-013).

Prices live in backend/config/prices.json, per million input and output tokens for each model, so
they can be updated without changing code. Amounts are Decimal from the file to the database:
float would add rounding errors to money.
"""

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

PRICES_FILE = Path(__file__).resolve().parents[2] / "config" / "prices.json"
MILLION = Decimal(1_000_000)
# llm_usage.cost_usd is NUMERIC(10,6).
COST_PRECISION = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPrice:
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal


def load_prices(path: Path = PRICES_FILE) -> dict[str, ModelPrice]:
    """Read the price table. Prices are written as strings in the file ("0.10") so that they are
    never parsed as float."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        model: ModelPrice(
            input_per_million_usd=Decimal(price["input_per_million_usd"]),
            output_per_million_usd=Decimal(price["output_per_million_usd"]),
        )
        for model, price in raw.items()
    }


@lru_cache
def get_prices() -> dict[str, ModelPrice]:
    return load_prices()


def cost_usd(price: ModelPrice, input_tokens: int, output_tokens: int) -> Decimal:
    """Estimated cost of one call, rounded to the precision of llm_usage.cost_usd."""
    cost = (
        input_tokens * price.input_per_million_usd + output_tokens * price.output_per_million_usd
    ) / MILLION
    return cost.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)
