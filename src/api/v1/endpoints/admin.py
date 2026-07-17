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


# ---------------------------------------------------------------------------
# Cluster status info
# ---------------------------------------------------------------------------

@router.get("/cluster", summary="Inference cluster health and telemetry")
def get_cluster_status(admin_user: UserSession = Depends(require_admin)):
    """[Admin] Return health and telemetry data for all nodes across providers (ollama, vllm)."""
    providers = ["ollama", "vllm"]
    cluster_data = {}
    
    for provider in providers:
        health_nodes = redis_service.get_all_node_health(provider)
        telemetry_nodes = redis_service.get_all_node_telemetry(provider)
        
        # Merge health and telemetry by node_id
        nodes_dict = {}
        for hn in health_nodes:
            nid = hn.get("node_id")
            if nid:
                nodes_dict[nid] = {
                    "id": nid,
                    "url": hn.get("url"),
                    "status": hn.get("status", "unknown"),
                    "latency_ms": int(hn.get("latency_ms", 0)),
                    "models": [m.strip() for m in hn.get("models", "").split(",") if m.strip()],
                    "active_requests": 0,
                    "vram_used_mb": 0.0,
                    "tokens_per_sec": 0.0,
                    "ttft_ms": 0
                }
        
        for tn in telemetry_nodes:
            nid = tn.get("node_id")
            if nid:
                if nid not in nodes_dict:
                    nodes_dict[nid] = {
                        "id": nid,
                        "url": tn.get("url", ""),
                        "status": tn.get("status", "unknown"),
                        "latency_ms": int(tn.get("latency_ms", 0)),
                        "models": [],
                        "active_requests": 0,
                        "vram_used_mb": 0.0,
                        "tokens_per_sec": 0.0,
                        "ttft_ms": 0
                    }
                try:
                    nodes_dict[nid]["active_requests"] = int(tn.get("active_requests", 0))
                    nodes_dict[nid]["vram_used_mb"] = float(tn.get("vram_used_mb", 0.0))
                    nodes_dict[nid]["tokens_per_sec"] = float(tn.get("tokens_per_sec", 0.0))
                    nodes_dict[nid]["ttft_ms"] = int(tn.get("ttft_ms", 0))
                except (ValueError, TypeError):
                    pass
                
        cluster_data[provider] = list(nodes_dict.values())
        
    return cluster_data

