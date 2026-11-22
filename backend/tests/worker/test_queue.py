"""enqueue_job and the worker's registration of tasks. No Redis and no database needed."""

import uuid
from typing import Any

import pytest
from arq.worker import func

from app.db.models import Job, JobType
from app.worker.queue import enqueue_job
from app.worker.settings import WorkerSettings
from app.worker.tasks import JOB_TIMEOUT_SECONDS, process_book

pytestmark = pytest.mark.anyio


class FakePool:
    """Records what would be sent to Redis. ARQ returns None when the job ID is already queued."""

    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> object | None:
        self.calls.append((function, args, kwargs))
        return object() if self.accepted else None


def make_job() -> Job:
    return Job(id=uuid.uuid4(), book_id=uuid.uuid4(), type="process_book", status="queued")


async def test_enqueue_uses_job_type_and_job_id() -> None:
    pool, job = FakePool(), make_job()

    queued = await enqueue_job(pool, job)  # type: ignore[arg-type]

    assert queued is True
    assert pool.calls == [("process_book", (str(job.id),), {"_job_id": str(job.id)})]


async def test_enqueue_returns_false_when_already_queued() -> None:
    assert await enqueue_job(FakePool(accepted=False), make_job()) is False  # type: ignore[arg-type]


def test_worker_registers_process_book() -> None:
    assert process_book in WorkerSettings.functions
    # One more than MAX_TRIES: the extra try only marks the job failed (see process_book).
    assert WorkerSettings.max_tries == 4
    assert WorkerSettings.job_timeout == JOB_TIMEOUT_SECONDS
    assert WorkerSettings.keep_result == 0
    # enqueue_job() uses the job type as the task name, so the two must be the same string.
    assert func(process_book).name == JobType.PROCESS_BOOK.value
