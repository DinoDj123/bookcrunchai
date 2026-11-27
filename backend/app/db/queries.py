"""Queries that the API and the worker both need, so that they answer them the same way."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobType, Summary


async def latest_summary_job(session: AsyncSession, summary: Summary) -> Job | None:
    """The newest generate_summary job of a summary. A summary that failed and was requested again
    has older jobs too; only the newest one may write or fail the summary."""
    return await session.scalar(
        select(Job)
        .where(
            Job.book_id == summary.book_id,
            Job.type == JobType.GENERATE_SUMMARY,
            Job.params["summary_id"].astext == str(summary.id),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
