"""
6-Tier Rate Limiting Engine
============================
Production-grade rate limiting stack for enterprise CPaaS.

Tiers (checked in order — any tier blocks = 429):

    1. Gateway-wide    rate:gateway              → global cap
    2. Per-IP          rate:ip:{ip}              → DDoS protection
    3. Per-Org         rate:org:{org_id}          → from org plan
    4. Per-User        rate:user:{user_id}        → from user plan
    5. Per-API-Key     rate:apikey:{key_hash}     → from key's plan
    6. Per-Model       rate:model:{model}         → per-model concurrency

All tiers use the same sliding-window ZSET implementation from
RedisService.sliding_window_check() — just different key prefixes.

The 429 response includes an X-RateLimit-Tier header indicating
which tier blocked the request (for client debugging).
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status

from src.core.config import settings
from src.services.redis_service import redis_service

logger = logging.getLogger("rate_limiter")


@dataclass
class RateLimitResult:
    """Result of the 6-tier rate limit check."""
    allowed: bool
    blocked_by: Optional[str] = None   # tier name that blocked


def enforce_rate_limits(
    user_id: int,
    ip_address: str,
    organization_id: Optional[int] = None,
    api_key_hash: Optional[str] = None,
    model: Optional[str] = None,
    rps: int = 5,
    daily_quota: int = 1000,
    monthly_quota: int = 30_000,
) -> RateLimitResult:
    """
    Run the 6-tier rate limiting stack.

    Each tier is a sliding-window ZSET check against Redis.
    If any tier blocks, an HTTPException(429) is raised immediately
    with a descriptive X-RateLimit-Tier header.

    Args:
        user_id:         Authenticated user ID
        ip_address:      Client IP (from request.client.host)
        organization_id: Org ID (from session, if multi-tenant)
        api_key_hash:    SHA-256 prefix of the API key (if sk_ auth)
        model:           Target model name (e.g. "gemma2:2b")
        rps:             Per-user requests/second from plan
        daily_quota:     Per-user daily request quota from plan
        monthly_quota:   Per-user monthly request quota from plan
    """

    # ── Tier 1: Gateway-wide ─────────────────────────────────────────────
    gateway_limit = settings.GATEWAY_RPS_LIMIT
    if not redis_service.sliding_window_check(
        key="rate:gateway", limit=gateway_limit, window=1
    ):
        _raise_429("gateway", f"Gateway capacity exceeded ({gateway_limit} req/s)")

    # ── Tier 2: Per-IP ───────────────────────────────────────────────────
    ip_limit = settings.IP_RPS_LIMIT
    if not redis_service.sliding_window_check(
        key=f"rate:ip:{ip_address}", limit=ip_limit, window=1
    ):
        _raise_429("ip", f"IP rate limit exceeded ({ip_limit} req/s)")

    # ── Tier 3: Per-Organization ─────────────────────────────────────────
    if organization_id:
        # Org-level limit = aggregate of all users in the org
        # Use 10x the user rps as the org limit (configurable per org in future)
        org_limit = rps * 10
        if not redis_service.sliding_window_check(
            key=f"rate:org:{organization_id}", limit=org_limit, window=1
        ):
            _raise_429("organization", f"Organization rate limit exceeded ({org_limit} req/s)")

    # ── Tier 4: Per-User ─────────────────────────────────────────────────
    if not redis_service.sliding_window_check(
        key=f"rate:user:{user_id}", limit=rps, window=1
    ):
        _raise_429("user", f"User rate limit exceeded ({rps} req/s)")

    # ── Tier 5: Per-API-Key ──────────────────────────────────────────────
    if api_key_hash:
        if not redis_service.sliding_window_check(
            key=f"rate:apikey:{api_key_hash[:16]}", limit=rps, window=1
        ):
            _raise_429("apikey", f"API key rate limit exceeded ({rps} req/s)")

    # ── Tier 6: Per-Model ────────────────────────────────────────────────
    if model:
        model_limits = _parse_model_limits()
        model_limit = model_limits.get(model, 100)  # default 100 req/s per model
        if not redis_service.sliding_window_check(
            key=f"rate:model:{model}", limit=model_limit, window=1
        ):
            _raise_429("model", f"Model '{model}' rate limit exceeded ({model_limit} req/s)")

    redis_service.incr_gateway_stat("requests")
    return RateLimitResult(allowed=True)


def _raise_429(tier: str, detail: str) -> None:
    """Raise a 429 with the blocking tier in the headers."""
    redis_service.incr_gateway_stat("rate_limited")
    logger.warning("[RATE LIMIT] [%s] %s", tier.upper(), detail)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"X-RateLimit-Tier": tier},
    )


def _parse_model_limits() -> dict:
    """Parse MODEL_RATE_LIMITS JSON env var into a dict."""
    try:
        return json.loads(settings.MODEL_RATE_LIMITS)
    except (json.JSONDecodeError, TypeError):
        return {}
