"""Rate limits (specification 4.2: login, upload and question endpoints).

A fixed window counted in Redis: the first request creates a counter that expires when the window
ends, every counted request adds one, and a request over the limit gets 429 RATE_LIMITED. Redis is
shared by all API processes, so the limit holds with several of them.

Login and registration count every attempt. Upload counts only the work that was really started
(a stored book or a retry): the limit is checked before the request body is read, and the counter
is raised after the book is queued. A rejected file or a repeated upload of the same file costs
the user nothing.
"""

import logging
import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, Request

from app.api.deps import CurrentUser, SettingsDep
from app.core.errors import AppError

logger = logging.getLogger(__name__)

LOGIN_WINDOW_SECONDS = 60
REGISTER_WINDOW_SECONDS = 60
UPLOAD_WINDOW_SECONDS = 60 * 60


class RequestCounter:
    """Counts requests per key in Redis, on the connection the job queue already uses."""

    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def hit(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Count one request; return the count in the current window and the seconds until the
        window ends."""
        # One transaction, so a counter can never be left without an expiry (it would block its
        # owner for ever). SET ... NX creates the counter with its expiry only if there is none,
        # so only the first request of a window starts it. (EXPIRE ... NX would be shorter, but
        # it needs Redis 7.)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=window_seconds, nx=True)
            pipe.incr(key)
            pipe.ttl(key)
            _, count, seconds_left = await pipe.execute()
        return int(count), max(int(seconds_left), 1)

    async def peek(self, key: str) -> tuple[int, int]:
        """The count in the current window and the seconds until it ends, without counting."""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.ttl(key)
            count, seconds_left = await pipe.execute()
        return int(count or 0), max(int(seconds_left), 1)


def get_request_counter(request: Request) -> RequestCounter:
    return RequestCounter(request.app.state.queue)


CounterDep = Annotated[RequestCounter, Depends(get_request_counter)]


def rate_limited(seconds_left: int) -> AppError:
    return AppError(
        429,
        "RATE_LIMITED",
        "Too many requests. Try again later.",
        {"retry_after_seconds": seconds_left},
        # The standard header, for clients that do not read our error body.
        headers={"Retry-After": str(seconds_left)},
    )


async def _count(
    counter: RequestCounter, name: str, subject: str, window_seconds: int, *, add: bool
) -> tuple[int, int]:
    """The count for one subject (an address, a user) of the limit `name`, after adding this
    request if `add` is set."""
    key = f"rate:{name}:{subject}"
    try:
        if add:
            return await counter.hit(key, window_seconds)
        return await counter.peek(key)
    except Exception as error:
        # Redis is down. Letting the request through keeps login working; a refused request
        # here would turn a Redis outage into an outage of the whole application.
        # Only the limit's name is logged: the subject is an IP address or a user ID
        # (specification 4.2, no personal data in logs). No traceback either, this happens on
        # every request while Redis is away.
        logger.warning("Rate limit '%s' could not be checked: %r", name, error)
        return 0, 1


def client_address(request: Request) -> str:
    """The browser's address. The API is reachable only through Caddy, which replaces any
    X-Forwarded-For the client sent with the address it saw; uvicorn runs with --proxy-headers
    (docker-compose.yml) and puts that address here. So a client cannot pick its own counter by
    sending the header itself."""
    return request.client.host if request.client else "unknown"


async def limit_logins(request: Request, counter: CounterDep, settings: SettingsDep) -> None:
    """Per client address, failed or not: slows down password guessing. Not per email, because
    then anyone could lock another user out by guessing at their address."""
    count, seconds_left = await _count(
        counter, "login", client_address(request), LOGIN_WINDOW_SECONDS, add=True
    )
    if count > settings.login_rate_limit:
        raise rate_limited(seconds_left)


async def limit_registrations(request: Request, counter: CounterDep, settings: SettingsDep) -> None:
    """Per client address, successful or not: keeps a script from filling the database with
    accounts, each of which costs a password hash and gets its own upload limit."""
    count, seconds_left = await _count(
        counter, "register", client_address(request), REGISTER_WINDOW_SECONDS, add=True
    )
    if count > settings.register_rate_limit:
        raise rate_limited(seconds_left)


async def limit_uploads(user: CurrentUser, counter: CounterDep, settings: SettingsDep) -> None:
    """Per user: every processed book is a file on disk and, later, paid LLM work. Refuses the
    request if the user has used up the limit; count_upload() does the counting.

    Checking and counting are two steps, so requests sent at the same moment can pass together
    and go over the limit by a few. That is acceptable for an abuse limit.
    """
    count, seconds_left = await _count(
        counter, "upload", str(user.id), UPLOAD_WINDOW_SECONDS, add=False
    )
    if count >= settings.upload_rate_limit:
        raise rate_limited(seconds_left)


async def count_upload(counter: RequestCounter, user_id: uuid.UUID) -> None:
    """Count one started processing (a new book, or a retry of a failed one) for the user."""
    await _count(counter, "upload", str(user_id), UPLOAD_WINDOW_SECONDS, add=True)
