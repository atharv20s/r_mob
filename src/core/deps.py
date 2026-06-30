"""
src/core/deps.py
================
FastAPI dependency functions.

Request hot path (95% of traffic):
    JWT  →  Redis blacklist  →  Decode  →  Redis session  →  Redis rate-limit
    →  Redis quota  →  Endpoint

SQL cold path (session expired / first login):
    JWT  →  Redis blacklist  →  Decode  →  SQL User  →  SQL APIKey+Plan
    →  Rebuild Redis session  →  Redis rate-limit  →  Redis quota  →  Endpoint
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Generator
from datetime import datetime, UTC

from src.db.session import SessionLocal
from src.db.models import User, APIKey, Plan, UserRole, UsageRecord
from src.core.config import settings
from src.core.security import decode_access_token
from src.core.schemas import UserSession
from src.services.redis_service import redis_service

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login"
)


def get_db() -> Generator[Session, None, None]:
    """Dependency to retrieve a new database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> UserSession:
    """
    Authenticate the current request and always return a ``UserSession``.

    Fast path (Redis session warm) — zero SQL queries:
        Returns a UserSession built from HGETALL session:{user_id}.

    Cold path (session missing/expired) — one SQL round-trip:
        Queries User + APIKey + Plan, rebuilds the Redis session,
        returns a UserSession.  The next request will be fast.
    """
    # ── 1. Blacklist check ───────────────────────────────────────────────────
    if redis_service.is_blacklisted(token):
        redis_service.incr_gateway_stat("unauthorized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked/logged out."
        )

    # ── 2. Decode JWT ────────────────────────────────────────────────────────
    user_id_str = decode_access_token(token)
    if not user_id_str:
        redis_service.incr_gateway_stat("unauthorized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate token credentials."
        )

    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload identifiers."
        )

    # ── 3. Redis fast path ───────────────────────────────────────────────────
    session = redis_service.get_session(user_id)
    if session:
        return UserSession(
            id=int(session["user_id"]),
            email=session.get("email", ""),
            role=session.get("role", "user"),
            is_active=True,
            plan=session.get("plan", "free"),
            rps=int(session.get("rps", 5)),
            daily_quota=int(session.get("daily_quota", 1000)),
            monthly_quota=int(session.get("monthly_quota", 30000)),
        )

    # ── 4. SQL cold path — rebuild session ───────────────────────────────────
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found."
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is inactive."
        )

    # Fetch plan to store in session (paid once, cached for 30 min)
    api_key_record = (
        db.query(APIKey)
        .filter(APIKey.user_id == user.id, APIKey.is_active == True)
        .first()
    )
    if api_key_record and api_key_record.plan_rel:
        plan = api_key_record.plan_rel
    else:
        plan = db.query(Plan).filter(Plan.name == "free").first()

    plan_name     = plan.name              if plan else "free"
    rps           = plan.requests_per_sec  if plan else 5
    daily_quota   = plan.daily_quota       if plan else 1000
    monthly_quota = plan.monthly_quota     if plan else 30_000

    ttl = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    now = int(datetime.now(UTC).timestamp())

    redis_service.create_session(user.id, {
        "user_id":        user.id,
        "email":          user.email,
        "role":           user.role if isinstance(user.role, str) else user.role.value,
        "plan":           plan_name,
        "rps":            rps,
        "daily_quota":    daily_quota,
        "monthly_quota":  monthly_quota,
        "login_time":     now,
        "ip":             "unknown",
        "user_agent":     "unknown",
        "expires":        now + ttl,
    }, ttl=ttl)

    return UserSession(
        id=user.id,
        email=user.email,
        role=user.role if isinstance(user.role, str) else user.role.value,
        is_active=user.is_active,
        plan=plan_name,
        rps=rps,
        daily_quota=daily_quota,
        monthly_quota=monthly_quota,
    )


def check_rate_limit(
    user: UserSession = Depends(get_current_user),
) -> None:
    """
    Pure-Redis rate limiting and quota enforcement.

    Reads ``rps``, ``daily_quota``, ``monthly_quota`` from the
    ``UserSession`` (already loaded from Redis by ``get_current_user``).
    No SQL queries.
    """
    # Plan limits come directly from the session — no SQL needed
    rps           = getattr(user, "rps",           5)
    daily_quota   = getattr(user, "daily_quota",   1000)
    monthly_quota = getattr(user, "monthly_quota", 30_000)

    # ── Sliding-window rate limit ─────────────────────────────────────────
    allowed = redis_service.sliding_window_limit(
        user_id=str(user.id), limit=rps, window=1
    )
    if not allowed:
        redis_service.incr_gateway_stat("rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    # ── Daily quota (Redis INCR counter) ─────────────────────────────────
    today_str = datetime.now(UTC).date().isoformat()
    current_daily = redis_service.get_quota(user.id, today_str)

    if current_daily >= daily_quota:
        redis_service.incr_gateway_stat("rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily quota exceeded"
        )

    # ── Monthly quota (Redis usage HASH — updated by usage_flusher) ───────
    # Sum current month's days from Redis usage keys
    today = datetime.now(UTC).date()
    start_of_month = today.replace(day=1)
    monthly_used = _sum_monthly_usage_from_redis(user.id, start_of_month, today)

    if monthly_used >= monthly_quota:
        redis_service.incr_gateway_stat("rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Monthly quota exceeded. Allowed: {monthly_quota} requests/month."
        )


def _sum_monthly_usage_from_redis(user_id: int, start, end) -> int:
    """
    Sum request_count across all usage:{user_id}:{date} keys for the current month.

    This replaces the SQL SUM(UsageRecord) query in the old check_rate_limit.
    Iterates at most ~30 Redis HGET calls — fast and entirely in Redis.
    """
    from datetime import timedelta
    total = 0
    current = start
    while current <= end:
        date_str = current.isoformat()
        usage = redis_service.get_usage(user_id, date_str)
        total += usage.get("request_count", 0)
        current += timedelta(days=1)
    return total


def require_admin(user: UserSession = Depends(get_current_user)) -> UserSession:
    """Dependency that checks if the current authenticated user has admin privileges."""
    role = user.role if isinstance(user.role, str) else user.role.value
    if role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin role required."
        )
    return user
