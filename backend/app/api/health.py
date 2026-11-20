from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness check used by Docker and the deployment step. Does not touch the database."""
    return {"status": "ok"}
