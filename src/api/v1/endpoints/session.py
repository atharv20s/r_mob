from fastapi import APIRouter, Depends, HTTPException, status

from src.db.models import User
from src.core.deps import get_current_user
from src.services.redis_service import redis_service

router = APIRouter()


@router.get("/session")
def get_session(user: User = Depends(get_current_user)):
    """Retrieve the current user's Redis session data."""
    session_data = redis_service.get_session(user.id)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active session found. Please login again."
        )
    return session_data
