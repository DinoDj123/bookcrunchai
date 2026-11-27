"""One real call to the Claude API, to check ANTHROPIC_API_KEY and the model names by hand.

    docker compose exec worker python scripts/llm_smoke.py            # CLAUDE_MODEL_FAST
    docker compose exec worker python scripts/llm_smoke.py strong     # CLAUDE_MODEL_STRONG

Costs a fraction of a cent. Never run by the tests or CI. It calls the SDK directly and writes
nothing to the database, because a usage row needs a book.
"""

import asyncio
import sys

import anthropic

from app.core.config import get_settings
from app.llm.pricing import cost_usd, get_prices


async def main() -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set (see .env.example)")
        return 1
    strong = len(sys.argv) > 1 and sys.argv[1] == "strong"
    model = settings.claude_model_strong if strong else settings.claude_model_fast

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=60)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": "Reply with the single word: pong"}],
        )
    except anthropic.AuthenticationError:
        print("The key was rejected (401). Check ANTHROPIC_API_KEY.")
        return 1
    except anthropic.NotFoundError:
        print(f"Model {model!r} does not exist (404). Check CLAUDE_MODEL_FAST/_STRONG.")
        return 1
    except anthropic.APIStatusError as error:
        print(f"The API answered {error.status_code}: {error.message}")
        return 1
    except anthropic.APIConnectionError as error:
        print(f"Could not reach the API: {error}")
        return 1

    text = "".join(block.text for block in response.content if block.type == "text")
    usage = response.usage
    print(f"model:  {model}")
    print(f"answer: {text.strip()}")
    print(f"tokens: {usage.input_tokens} in, {usage.output_tokens} out")
    price = get_prices().get(model)
    if price is None:
        print(
            f"WARNING: {model!r} has no price in config/prices.json; the pipeline would refuse it"
        )
        return 1
    print(f"cost:   {cost_usd(price, usage.input_tokens, usage.output_tokens)} USD")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
