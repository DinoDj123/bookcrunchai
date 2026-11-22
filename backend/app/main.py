"""FastAPI application. Run locally with: uvicorn app.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.errors import register_error_handlers
from app.worker.queue import create_redis_pool

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One connection to the job queue for the whole life of the API (app.api.deps.get_queue)."""
    app.state.queue = await create_redis_pool()
    yield
    await app.state.queue.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="bookcrunch.ai API",
        version="0.1.0",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)
    return app


app = create_app()
