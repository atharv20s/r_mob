from fastapi import APIRouter, Depends

from src.db.models import User
from src.core.deps import get_current_user
from src.services.redis_service import redis_service

router = APIRouter()


@router.get("/context")
def get_context(user: User = Depends(get_current_user)):
    """Retrieve the current user's conversation context from Redis."""
    history = redis_service.get_context(user.id)
    return {
        "user_id": user.id,
        "message_count": len(history),
        "messages": history,
    }


@router.delete("/context")
def clear_context(user: User = Depends(get_current_user)):
    """Clear the current user's conversation context in Redis."""
    redis_service.clear_context(user.id)
    return {"message": "Conversation context cleared."}
