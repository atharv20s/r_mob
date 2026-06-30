from fastapi import APIRouter, Depends
from typing import List, Dict, Any

from src.core.deps import require_admin
from src.core.schemas import UserSession
from src.services.redis_service import redis_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Rate limit metrics
# ---------------------------------------------------------------------------

@router.get("/redis/rate-limit", summary="Active rate-limit key count")
def rate_limit_metrics(admin_user: UserSession = Depends(require_admin)):
    """[Admin] Return count of active rate-limit keys in Redis."""
    active = redis_service.get_active_rate_limit_keys()
    return {"active_keys": active}


# ---------------------------------------------------------------------------
# Cache statistics
# ---------------------------------------------------------------------------

@router.get("/cache/stats", summary="Cache hit/miss statistics")
def cache_stats(admin_user: UserSession = Depends(require_admin)):
    """[Admin] Return cache hit/miss statistics from Redis."""
    return redis_service.get_cache_stats()


# ---------------------------------------------------------------------------
# Redis key inspection
# ---------------------------------------------------------------------------

@router.get("/redis/keys", summary="Categorised Redis key counts")
def redis_key_inspection(admin_user: UserSession = Depends(require_admin)):
    """[Admin] Return categorised counts of all Redis keys."""
    all_keys = redis_service.get_all_keys("*")
    categorized: Dict[str, int] = {
        "blacklist_keys":  0,
        "session_keys":    0,
        "cache_keys":      0,
        "context_keys":    0,
        "quota_keys":      0,
        "usage_keys":      0,
        "audit_keys":      0,
        "rate_limit_keys": 0,
        "stats_keys":      0,
        "other_keys":      0,
    }
    for key in all_keys:
        prefix = key.split(":")[0]
        mapping = {
            "blacklist":        "blacklist_keys",
            "session":          "session_keys",
            "cache":            "cache_keys",
            "context":          "context_keys",
            "quota":            "quota_keys",
            "usage":            "usage_keys",
            "audit":            "audit_keys",
            "rate_limit":       "rate_limit_keys",
            "rate_limit_slide": "rate_limit_keys",
            "stats":            "stats_keys",
        }
        bucket = mapping.get(prefix, "other_keys")
        categorized[bucket] += 1

    categorized["total_keys"] = len(all_keys)
    return categorized


# ---------------------------------------------------------------------------
# Redis server info
# ---------------------------------------------------------------------------

@router.get("/redis/info", summary="Redis server metrics")
def redis_server_info(admin_user: UserSession = Depends(require_admin)):
    """[Admin] Return Redis server metrics (version, memory, uptime)."""
    return redis_service.redis_info()


# ---------------------------------------------------------------------------
# Full key inspector — powers the portal Redis Inspector tab
# ---------------------------------------------------------------------------

@router.get(
    "/redis/inspect",
    summary="Full Redis key inspection",
    response_model=List[Dict[str, Any]],
)
def inspect_redis_keys(admin_user: UserSession = Depends(require_admin)):
    """
    [Admin] Return all live Redis keys with type, TTL, and value preview.
    Used by the /portal Redis Inspector tab.
    """
    return redis_service.inspect_all_keys()


# ---------------------------------------------------------------------------
# Gateway statistics
# ---------------------------------------------------------------------------

@router.get(
    "/redis/gateway-stats",
    summary="Live gateway statistics",
)
def gateway_stats(admin_user: UserSession = Depends(require_admin)):
    """
    [Admin] Return live gateway counters from the stats:gateway Redis HASH.

    Fields:
        requests       — total requests processed
        cache_hits     — responses served from Redis cache
        cache_misses   — requests forwarded to the LLM
        hit_ratio      — cache efficiency percentage
        rate_limited   — 429 responses issued
        unauthorized   — 401 responses issued
        errors         — LLM or server errors
        avg_latency_ms — average LLM response time (cache misses only)
    """
    return redis_service.get_gateway_stats()


# ---------------------------------------------------------------------------
# Audit buffer stats
# ---------------------------------------------------------------------------

@router.get("/redis/audit-buffer", summary="Audit log buffer depth")
def audit_buffer_stats(admin_user: UserSession = Depends(require_admin)):
    """[Admin] Return the current number of unbuffered audit log entries."""
    return {"buffered_entries": redis_service.audit_buffer_length()}


# ---------------------------------------------------------------------------
# Flush — demo/testing only, admin-gated
# ---------------------------------------------------------------------------

@router.delete(
    "/redis/flush",
    summary="Flush all Redis keys (demo/testing only)",
)
def flush_redis(admin_user: UserSession = Depends(require_admin)):
    """
    [Admin] Delete ALL keys from the Redis database.
    ⚠  Use only in dev/demo environments.
    """
    deleted = redis_service.flush_all()
    return {
        "message": "Redis flushed successfully.",
        "keys_deleted": deleted,
    }
