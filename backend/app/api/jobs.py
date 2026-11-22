"""Job status (FR-012). The frontend polls this every 2-3 seconds while a job is running."""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import AppError
from app.db.models import Book, Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    book_id: uuid.UUID
    type: str
    status: str
    stage: str | None
    progress: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> JobOut:
    # Ownership is part of the query: a job of another user's book is simply not found, so the
    # answer does not reveal that it exists (specification 7.3). Demo books belong to everyone.
    job = await session.scalar(
        select(Job)
        .join(Book, Book.id == Job.book_id)
        .where(Job.id == job_id, or_(Book.owner_id == user.id, Book.is_demo))
    )
    if job is None:
        raise AppError(404, "JOB_NOT_FOUND", "Job not found.")
    return JobOut.model_validate(job)
