from fastapi import APIRouter, HTTPException

from src.services.redis_service import redis_service

router = APIRouter()


@router.get("/redis")
def health_redis():
    """Check Redis connectivity and return health status."""
    if redis_service.ping():
        return {"status": "healthy", "redis": "connected"}
    raise HTTPException(
        status_code=503,
        detail={"status": "unhealthy", "redis": "disconnected"},
    )
