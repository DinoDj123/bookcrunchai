"""Rate limits (specification 4.2: login, upload and question endpoints).

A fixed window counted in Redis: the first request creates a counter that expires when the window
ends, every request adds one, and a request that pushes the counter over the limit gets
429 RATE_LIMITED. Redis is shared by all API processes, so the limit holds with several of them.
"""

import logging
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, Request

from app.api.deps import CurrentUser, SettingsDep
from app.core.errors import AppError

logger = logging.getLogger(__name__)

LOGIN_WINDOW_SECONDS = 60
UPLOAD_WINDOW_SECONDS = 60 * 60


class RequestCounter:
    """Counts requests per key in Redis, on the connection the job queue already uses."""

    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def hit(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Count one request; return the count in the current window and the seconds until the
        window ends."""
        # One transaction, so a counter can never be left without an expiry (it would block its
        # owner for ever). NX: only the first request of a window sets the expiry.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            pipe.ttl(key)
            count, _, seconds_left = await pipe.execute()
        return int(count), max(int(seconds_left), 1)


def get_request_counter(request: Request) -> RequestCounter:
    return RequestCounter(request.app.state.queue)


CounterDep = Annotated[RequestCounter, Depends(get_request_counter)]


async def _check(counter: RequestCounter, key: str, limit: int, window_seconds: int) -> None:
    try:
        count, seconds_left = await counter.hit(f"rate:{key}", window_seconds)
    except Exception:
        # Redis is down. Letting the request through keeps login working; a refused request
        # here would turn a Redis outage into an outage of the whole application.
        logger.exception("Rate limit for %s could not be checked", key)
        return
    if count > limit:
        raise AppError(
            429,
            "RATE_LIMITED",
            "Too many requests. Try again later.",
            {"retry_after_seconds": seconds_left},
        )


async def limit_logins(request: Request, counter: CounterDep, settings: SettingsDep) -> None:
    """Per client address, failed or not: slows down password guessing. Not per email, because
    then anyone could lock another user out by guessing at their address."""
    # Behind Caddy, uvicorn runs with --proxy-headers, so this is the browser's address.
    address = request.client.host if request.client else "unknown"
    await _check(counter, f"login:{address}", settings.login_rate_limit, LOGIN_WINDOW_SECONDS)


async def limit_uploads(user: CurrentUser, counter: CounterDep, settings: SettingsDep) -> None:
    """Per user: every upload is a file on disk and, later, paid LLM work."""
    await _check(counter, f"upload:{user.id}", settings.upload_rate_limit, UPLOAD_WINDOW_SECONDS)
