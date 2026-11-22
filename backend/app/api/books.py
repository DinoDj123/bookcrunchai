"""Book endpoints. This task: upload (FR-002) with de-duplication per user (FR-014)."""

import hashlib
import logging
import re
import uuid
from pathlib import Path

from arq.connections import ArqRedis
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.api.deps import CurrentUser, QueueDep, SessionDep, SettingsDep
from app.core.errors import AppError
from app.db.models import Book, BookStatus, Job, JobStatus, JobType
from app.parsing import FileFormat, normalize_text
from app.worker.queue import enqueue_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/books", tags=["books"])

# Extension -> format and the bytes every file of that format starts with (the same signatures
# the parser uses, docs/contracts/parse_book.md section 1).
FORMATS = {".epub": FileFormat.EPUB, ".pdf": FileFormat.PDF}
SIGNATURES = {FileFormat.EPUB: b"PK\x03\x04", FileFormat.PDF: b"%PDF"}

CHUNK_BYTES = 1024 * 1024
# A multipart request is a little larger than the file inside it (boundaries, part headers).
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
TITLE_MAX_LENGTH = 500  # books.title

# The handler reads the form itself (see upload_book), so FastAPI cannot work this out.
UPLOAD_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }
        }
    },
}


class BookBrief(BaseModel):
    id: uuid.UUID
    title: str
    status: str


class JobBrief(BaseModel):
    id: uuid.UUID
    status: str


class UploadOut(BaseModel):
    book: BookBrief
    job: JobBrief | None  # None only for an existing book that has no job


def unsupported_file() -> AppError:
    return AppError(415, "UNSUPPORTED_FILE_TYPE", "Only EPUB and PDF files can be uploaded.")


def file_too_large(max_bytes: int) -> AppError:
    megabytes = max(1, max_bytes // (1024 * 1024))
    return AppError(413, "FILE_TOO_LARGE", f"The file is larger than {megabytes} MB.")


def queue_unavailable() -> AppError:
    return AppError(
        503, "QUEUE_UNAVAILABLE", "The book could not be queued for processing. Try again."
    )


def title_from_file_name(file_name: str) -> str:
    """The title shown until the parser reads the real one from the book."""
    base_name = re.split(r"[/\\]", file_name)[-1]
    title = normalize_text(Path(base_name).stem.replace("_", " "))
    return title[:TITLE_MAX_LENGTH] or "Untitled"


async def store_upload(upload: UploadFile, path: Path, max_bytes: int) -> tuple[int, str, bytes]:
    """Copy the upload to path piece by piece and return its size, SHA-256 and first bytes.

    The framework has already received the request into a temporary file, so this is a second
    copy on disk, never the whole file in memory. Raises 413 if the file itself is over the limit.
    """
    digest = hashlib.sha256()
    size = 0
    first_bytes = b""
    with path.open("wb") as stored:
        while chunk := await upload.read(CHUNK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                raise file_too_large(max_bytes)
            first_bytes = first_bytes or chunk[:8]
            digest.update(chunk)
            await run_in_threadpool(stored.write, chunk)
    return size, digest.hexdigest(), first_bytes


async def find_book(session: AsyncSession, owner_id: uuid.UUID, file_sha256: str) -> Book | None:
    return await session.scalar(
        select(Book).where(Book.owner_id == owner_id, Book.file_sha256 == file_sha256)
    )


async def existing_upload(session: AsyncSession, queue: ArqRedis, book: Book) -> UploadOut:
    """The answer for a file this user already uploaded: the book and its latest job."""
    job = await session.scalar(
        select(Job).where(Job.book_id == book.id).order_by(Job.created_at.desc()).limit(1)
    )
    if job is not None and job.status == JobStatus.QUEUED:
        # Normally the job is already in the queue and this does nothing (same job ID). But if
        # the API stopped between saving the job and enqueuing it, nobody else would ever start
        # it, and every new upload of the file would end here.
        try:
            await enqueue_job(queue, job)
        except Exception as error:
            logger.exception("Job %s could not be queued", job.id)
            raise queue_unavailable() from error
    return UploadOut(
        book=BookBrief(id=book.id, title=book.title, status=book.status),
        job=JobBrief(id=job.id, status=job.status) if job else None,
    )


@router.post("", status_code=202, openapi_extra={"requestBody": UPLOAD_REQUEST_BODY})
async def upload_book(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> UploadOut:
    """Upload an EPUB or PDF (form field `file`, at most 50 MB) and queue its processing.

    202 with the new book and its job; 200 with the existing book if this user already uploaded
    the same file.
    """
    # The form is read here and not declared as a parameter: FastAPI would receive the whole
    # request before checking the session, so anyone could make the server store 50 MB per
    # request. This way an anonymous or oversized request is refused before its body is read.
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES:
        raise file_too_large(settings.max_upload_bytes)
    # Read now: after a rollback (below) the session would have to load the user again.
    owner_id = user.id

    async with request.form() as form:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise AppError(422, "VALIDATION_ERROR", "Send the book as the form field 'file'.")
        file_name = (file.filename or "").strip()
        file_format = FORMATS.get(Path(file_name).suffix.lower())
        if file_format is None:
            raise unsupported_file()

        # The stored name is generated: the name the user gave never decides where a file is
        # written.
        book_id = uuid.uuid4()
        stored_name = f"{book_id}.{file_format.value}"
        storage_dir = Path(settings.storage_dir)
        storage_dir.mkdir(parents=True, exist_ok=True)
        stored_path = storage_dir / stored_name

        try:
            size, file_sha256, first_bytes = await store_upload(
                file, stored_path, settings.max_upload_bytes
            )
            # The extension can be anything the user typed; the first bytes show what the file
            # is.
            if not first_bytes.startswith(SIGNATURES[file_format]):
                raise unsupported_file()

            existing = await find_book(session, owner_id, file_sha256)
            if existing is None:
                book = Book(
                    id=book_id,
                    owner_id=owner_id,
                    title=title_from_file_name(file_name),
                    file_format=file_format.value,
                    file_sha256=file_sha256,
                    file_path=stored_name,
                    file_size_bytes=size,
                    status=BookStatus.UPLOADED,
                )
                job = Job(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    type=JobType.PROCESS_BOOK,
                    status=JobStatus.QUEUED,
                )
                try:
                    session.add(book)
                    await session.flush()  # the job row needs the book row
                    session.add(job)
                    # Committed before enqueuing: the worker must find the job when it looks
                    # for it.
                    await session.commit()
                except IntegrityError:
                    # Another request with the same file from the same user was a moment faster
                    # (unique index on owner and hash). Answer as for a repeated upload.
                    await session.rollback()
                    existing = await find_book(session, owner_id, file_sha256)
                    if existing is None:
                        raise
            if existing is not None:
                stored_path.unlink(missing_ok=True)
                response.status_code = 200
                return await existing_upload(session, queue, existing)
        except BaseException:
            stored_path.unlink(missing_ok=True)
            raise

    try:
        await enqueue_job(queue, job)
    except Exception as error:
        # Without the queue nobody would process this book. Remove it again, so that the user can
        # upload the same file once the queue is back instead of getting a stuck book.
        logger.exception("Job %s could not be queued; removing book %s", job.id, book_id)
        await session.execute(delete(Book).where(Book.id == book_id))  # the job goes with it
        await session.commit()
        stored_path.unlink(missing_ok=True)
        raise queue_unavailable() from error

    return UploadOut(
        book=BookBrief(id=book.id, title=book.title, status=book.status),
        job=JobBrief(id=job.id, status=job.status),
    )
