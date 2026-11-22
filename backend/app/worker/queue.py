"""The API's side of the job queue: it writes a job row to PostgreSQL, then enqueues the job's ID
here (specification 5.4). Only the ID travels through Redis; the worker reads everything else
from the database.
"""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings
from app.db.models import Job


async def create_redis_pool() -> ArqRedis:
    """Connection to the queue; created once when the API starts."""
    return await create_pool(RedisSettings.from_dsn(get_settings().redis_url))


async def enqueue_job(redis: ArqRedis, job: Job) -> bool:
    """Queue a job that is already saved (and committed) in the database.

    The worker task has the same name as the job type. The ARQ job ID is our job ID, so the same
    job cannot be in the queue twice; False means it is already queued or running.
    """
    queued = await redis.enqueue_job(job.type, str(job.id), _job_id=str(job.id))
    return queued is not None
