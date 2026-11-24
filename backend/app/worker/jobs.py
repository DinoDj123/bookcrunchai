"""run_job: what every worker task does around its own work, so each task only writes the work.

    async def generate_summary(ctx: dict[str, Any], job_id: str) -> None:
        await run_job(ctx, job_id, _write_summary, on_failure=_mark_summary_failed)

    async def _write_summary(ctx: dict[str, Any], session: AsyncSession, job: Job) -> None:
        ...  # the work; the job is already "running"

What run_job does with the job (specification 4.5, Appendix C "Job retries"):
- A job that does not exist or is already done is skipped: the queue delivered it twice.
- The job becomes "running" (committed, so the UI sees it), then the handler runs.
- The handler returns: the job becomes "done", in the same commit as the handler's last changes.
- The handler raises JobFailed(code, message): the job fails at once with that code. For failures
  that would happen again the same way (a file that cannot be parsed, the cost limit).
- The handler raises anything else (database, network): ARQ runs the job again, MAX_TRIES tries in
  all; after the last one the job fails with INTERNAL_ERROR.
- The worker died during earlier tries: WorkerSettings allows one try more than MAX_TRIES, and
  that try only marks the job failed.

run_job touches only the jobs row. What a failed job means for the thing it works on (the book,
a summary) is the task's business: pass on_failure, which runs in the transaction that fails the
job.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from arq import Retry
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus

MAX_TRIES = 3

# Shown for failures the user can do nothing about; the real error goes to the log.
GENERIC_ERROR_MESSAGE = "Something went wrong while processing this book. Please try again."
INTERNAL_ERROR = "INTERNAL_ERROR"

# The work of a task. Gets ARQ's ctx, a session and the job, which is already "running". It may
# commit as it goes (progress, finished steps); changes it leaves uncommitted are committed
# together with the job becoming "done".
Handler = Callable[[dict[str, Any], AsyncSession, Job], Awaitable[None]]
# Called when the job fails, after its status, error_code and error_message are set and before
# the commit. Must not commit.
OnFailure = Callable[[AsyncSession, Job], Awaitable[None]]

logger = logging.getLogger(__name__)


class JobFailed(Exception):
    """Raised by a handler to fail its job now, without another try. code goes to
    jobs.error_code, user_message is shown to the user. Uncommitted changes are discarded."""

    def __init__(self, code: str, user_message: str = GENERIC_ERROR_MESSAGE) -> None:
        super().__init__(f"{code}: {user_message}")
        self.code = code
        self.user_message = user_message


async def run_job(
    ctx: dict[str, Any], job_id: str, handler: Handler, *, on_failure: OnFailure | None = None
) -> None:
    """Run handler for the job, keeping the jobs row up to date. See the module docstring.

    ctx comes from ARQ: session_factory is set in WorkerSettings.on_startup, job_try is the number
    of this attempt (1 on the first one).
    """
    job_try: int = ctx["job_try"]
    if job_try > MAX_TRIES:
        # Earlier tries ended without an answer (the worker was stopped or killed while they ran).
        logger.error("Job %s: no tries left, marking it failed", job_id)
        await _fail_in_new_session(ctx, job_id, on_failure)
        return
    try:
        async with ctx["session_factory"]() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            if job is None or job.status == JobStatus.DONE:
                return

            job.status = JobStatus.RUNNING
            job.progress = 0
            job.attempts = job_try
            job.started_at = datetime.now(UTC)
            job.finished_at = None
            job.error_code = None
            job.error_message = None
            await session.commit()

            try:
                await handler(ctx, session, job)
            except JobFailed as failure:
                await session.rollback()
                await session.refresh(job)  # a rollback leaves loaded rows unread
                await _fail(session, job, failure.code, failure.user_message, on_failure)
                return

            job.status = JobStatus.DONE
            job.progress = 100
            job.finished_at = datetime.now(UTC)
            await session.commit()
    except Exception as error:
        logger.exception("Job %s: try %s of %s failed", job_id, job_try, MAX_TRIES)
        if job_try < MAX_TRIES:
            # The job keeps its current status; ARQ runs it again after the delay.
            raise Retry(defer=5 * job_try) from error
        await _fail_in_new_session(ctx, job_id, on_failure)


async def _fail(
    session: AsyncSession, job: Job, code: str, message: str, on_failure: OnFailure | None
) -> None:
    job.status = JobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.finished_at = datetime.now(UTC)
    if on_failure is not None:
        await on_failure(session, job)
    await session.commit()


async def _fail_in_new_session(
    ctx: dict[str, Any], job_id: str, on_failure: OnFailure | None
) -> None:
    """Fail a job with INTERNAL_ERROR after its last try. A new session, because the one the try
    used may be broken. If this fails too (database still down), fail_stale_jobs cleans up later.
    """
    async with ctx["session_factory"]() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None or job.status == JobStatus.DONE:
            return
        await _fail(session, job, INTERNAL_ERROR, GENERIC_ERROR_MESSAGE, on_failure)
