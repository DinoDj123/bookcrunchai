"""FastAPI application. Run locally with: uvicorn app.main:app --reload"""

from fastapi import FastAPI

from app.api.router import api_router
from app.core.errors import register_error_handlers

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="bookcrunch.ai API",
        version="0.1.0",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    register_error_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)
    return app


app = create_app()
