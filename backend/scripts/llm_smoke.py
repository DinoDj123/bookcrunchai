"""One real call to the LLM server, to check LLM_BASE_URL, LLM_API_KEY and the model names by hand.

    docker compose exec worker python scripts/llm_smoke.py            # LLM_MODEL_FAST
    docker compose exec worker python scripts/llm_smoke.py strong     # LLM_MODEL_STRONG

Free with Ollama; a fraction of a cent with a paid provider. Never run by the tests or CI. It calls
the SDK directly and writes nothing to the database, because a usage row needs a book.
"""

import asyncio
import sys

import openai

from app.core.config import get_settings
from app.llm.factory import NO_KEY
from app.llm.pricing import cost_usd, get_prices


async def main() -> int:
    settings = get_settings()
    strong = len(sys.argv) > 1 and sys.argv[1] == "strong"
    model = settings.llm_model_strong if strong else settings.llm_model_fast

    client = openai.AsyncOpenAI(
        base_url=settings.llm_base_url, api_key=settings.llm_api_key or NO_KEY, timeout=120
    )
    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": "Reply with the single word: pong"}],
        )
    except openai.AuthenticationError:
        print("The key was rejected (401). Check LLM_API_KEY.")
        return 1
    except openai.NotFoundError:
        print(f"Model {model!r} was not found (404). Check LLM_MODEL_FAST/_STRONG")
        print("(with Ollama: `ollama pull <model>` first).")
        return 1
    except openai.APIStatusError as error:
        print(f"The server answered {error.status_code}: {error.message}")
        return 1
    except openai.APIConnectionError as error:
        print(f"Could not reach {settings.llm_base_url}: {error}")
        return 1

    text = (response.choices[0].message.content or "") if response.choices else ""
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    print(f"server: {settings.llm_base_url}")
    print(f"model:  {model}")
    print(f"answer: {text.strip()}")
    print(f"tokens: {input_tokens} in, {output_tokens} out")
    price = get_prices().get(model)
    if price is None:
        print(
            f"WARNING: {model!r} has no price in config/prices.json; the pipeline would refuse it"
        )
        return 1
    print(f"cost:   {cost_usd(price, input_tokens, output_tokens)} USD")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
