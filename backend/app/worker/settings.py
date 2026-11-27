"""ARQ worker. Run with: arq app.worker.settings.WorkerSettings

The API enqueues a job ID in Redis (app.worker.queue); the worker reads the job's input from
PostgreSQL and writes progress and results back there (specification section 5.4). Two tasks:
process_book (parse, chunk, chapter notes) and generate_summary (one book summary).
"""

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.worker.tasks import (
    JOB_TIMEOUT_SECONDS,
    MAX_TRIES,
    fail_stale_jobs,
    generate_summary,
    process_book,
)


async def ping(_ctx: dict[str, Any]) -> str:
    """Handy to check the queue end to end without touching the database."""
    return "pong"


async def on_startup(ctx: dict[str, Any]) -> None:
    """Values every task needs. Tests pass their own, so tasks never import them directly."""
    ctx["session_factory"] = SessionLocal
    ctx["storage_dir"] = get_settings().storage_dir


class WorkerSettings:
    functions = [process_book, generate_summary, ping]
    cron_jobs = [cron(fail_stale_jobs, minute=set(range(0, 60, 5)), run_at_startup=True)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = on_startup
    # One more than MAX_TRIES. ARQ also counts a try that died with the worker, and after its own
    # last try it drops the job without calling the task; the extra try lets process_book mark the
    # job failed instead.
    max_tries = MAX_TRIES + 1
    job_timeout = JOB_TIMEOUT_SECONDS
    # Parsing is CPU work in a thread; a few at a time, so a burst of uploads does not push
    # parses over their time limit.
    max_jobs = 2
    # Results live in PostgreSQL. A result kept in Redis would also stop the same job ID from
    # being queued again, which a retry of a failed job needs.
    keep_result = 0
