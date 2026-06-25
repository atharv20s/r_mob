from fastapi import APIRouter, Depends

from src.core.deps import require_admin
from src.db.models import User
from src.services.redis_service import redis_service

router = APIRouter()


@router.get("/redis/rate-limit")
def rate_limit_metrics(admin_user: User = Depends(require_admin)):
    """Return count of active rate-limit keys in Redis."""
    active = redis_service.get_active_rate_limit_keys()
    return {"active_keys": active}


@router.get("/cache/stats")
def cache_stats(admin_user: User = Depends(require_admin)):
    """Return cache hit/miss statistics from Redis."""
    stats = redis_service.get_cache_stats()
    return stats


@router.get("/redis/keys")
def redis_key_inspection(admin_user: User = Depends(require_admin)):
    """Return categorized counts of all Redis keys — for demo/inspection."""
    all_keys = redis_service.get_all_keys("*")
    categorized = {
        "blacklist_keys": 0,
        "session_keys": 0,
        "cache_keys": 0,
        "quota_keys": 0,
        "rate_limit_keys": 0,
        "stats_keys": 0,
        "other_keys": 0,
    }
    for key in all_keys:
        if key.startswith("blacklist:"):
            categorized["blacklist_keys"] += 1
        elif key.startswith("session:"):
            categorized["session_keys"] += 1
        elif key.startswith("cache:"):
            categorized["cache_keys"] += 1
        elif key.startswith("quota:"):
            categorized["quota_keys"] += 1
        elif key.startswith("rate_limit:"):
            categorized["rate_limit_keys"] += 1
        elif key.startswith("stats:"):
            categorized["stats_keys"] += 1
        else:
            categorized["other_keys"] += 1
    categorized["total_keys"] = len(all_keys)
    return categorized
