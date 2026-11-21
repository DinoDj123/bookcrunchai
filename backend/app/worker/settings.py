"""ARQ worker. Run with: arq app.worker.settings.WorkerSettings

The API enqueues a job ID in Redis (app.worker.queue); the worker reads the job's input from
PostgreSQL and writes progress and results back there (specification section 5.4).
generate_summary is added in a later task.
"""

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.worker.tasks import MAX_TRIES, process_book


async def ping(_ctx: dict[str, Any]) -> str:
    """Handy to check the queue end to end without touching the database."""
    return "pong"


async def on_startup(ctx: dict[str, Any]) -> None:
    """Values every task needs. Tests pass their own, so tasks never import them directly."""
    ctx["session_factory"] = SessionLocal
    ctx["storage_dir"] = get_settings().storage_dir


class WorkerSettings:
    functions = [process_book, ping]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = on_startup
    max_tries = MAX_TRIES
    # Seconds. Above PARSE_TIMEOUT_SECONDS, so the task's own limit fires first and can mark the
    # book failed.
    job_timeout = 600
    # Results live in PostgreSQL. A result kept in Redis would also stop the same job ID from
    # being queued again, which a retry of a failed job needs.
    keep_result = 0
