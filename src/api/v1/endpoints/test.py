from fastapi import APIRouter, Depends
from src.core.deps import get_current_user, check_rate_limit
from src.db.models import User

router = APIRouter()

@router.get("/rate-limit")
def test_rate_limit(
    user: User = Depends(get_current_user),
    _rate_limit: None = Depends(check_rate_limit)
):
    return {"success": True}
