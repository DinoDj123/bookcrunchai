"""All routes of API version 1, mounted under /api/v1 in app.main."""

from fastapi import APIRouter

from app.api import auth, books, health, jobs, library, summaries

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(books.router)
api_router.include_router(library.router)
api_router.include_router(summaries.router)
api_router.include_router(jobs.router)
