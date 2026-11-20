"""ARQ worker. Run with: arq app.worker.settings.WorkerSettings

The API enqueues a job ID in Redis; the worker reads the job's input from PostgreSQL and writes
progress and results back there (specification section 5.4). Pipeline tasks (process_book,
generate_summary) are added in later tasks.
"""

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings


async def ping(_ctx: dict[str, Any]) -> str:
    """Placeholder task so the worker starts; also handy to check the queue end to end."""
    return "pong"


class WorkerSettings:
    functions = [ping]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = 3
