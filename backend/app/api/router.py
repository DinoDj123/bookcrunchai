"""All routes of API version 1, mounted under /api/v1 in app.main."""

from fastapi import APIRouter

from app.api import auth, books, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(books.router)
