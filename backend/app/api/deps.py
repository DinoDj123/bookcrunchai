"""Dependencies shared by the routers."""

from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import read_session_token
from app.db.models import User
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
