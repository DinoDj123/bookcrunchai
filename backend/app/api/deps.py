"""Dependencies shared by the routers."""

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import read_session_token
from app.db.models import Book, User
from app.db.session import get_session

SESSION_COOKIE = "session"

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    """The one dependency every protected endpoint uses (specification 7.3): it reads the session
    cookie, verifies the token and loads the user. Anything wrong with any of the three is 401."""
    token = request.cookies.get(SESSION_COOKIE)
    user_id = read_session_token(token, settings.jwt_secret) if token else None
    user = await session.get(User, user_id) if user_id else None
    if user is None:
        raise AppError(401, "UNAUTHENTICATED", "Log in to continue.")
    return user


def get_queue(request: Request) -> ArqRedis:
    """The connection to the job queue, opened when the API starts (app.main)."""
    queue: ArqRedis = request.app.state.queue
    return queue


CurrentUser = Annotated[User, Depends(get_current_user)]
QueueDep = Annotated[ArqRedis, Depends(get_queue)]


async def get_readable_book(book_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> Book:
    """The book of a /books/{book_id}/... request, if this user may read it: their own book or a
    demo book (FR-015). Ownership is part of the query, so another user's book is simply not
    found and the answer does not reveal that it exists (specification 4.2).

    For reading only. An endpoint that changes a book must also check that the user owns it:
    demo books are read-only.
    """
    book = await session.scalar(
        select(Book).where(Book.id == book_id, or_(Book.owner_id == user.id, Book.is_demo))
    )
    if book is None:
        raise AppError(404, "BOOK_NOT_FOUND", "Book not found.")
    return book


ReadableBook = Annotated[Book, Depends(get_readable_book)]
