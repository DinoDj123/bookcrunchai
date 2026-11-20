"""All routes of API version 1, mounted under /api/v1 in app.main."""

from fastapi import APIRouter

from app.api import health

api_router = APIRouter()
api_router.include_router(health.router)
