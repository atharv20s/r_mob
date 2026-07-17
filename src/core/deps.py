"""
src/core/deps.py
================
FastAPI dependency functions.

Request hot path (95% of traffic):
    Token  →  Detect auth type (JWT vs sk_)  →  Redis session
    →  6-tier rate limit  →  Daily quota  →  Monthly quota  →  Endpoint

Auth methods:
    1. JWT Bearer — standard OAuth2 flow (login → access_token)
    2. API Key Bearer — programmatic access (Bearer sk_...)

Both produce the same UserSession dataclass, so endpoints don't care.

SQL cold path (session expired / first login):
    JWT:    Decode → SQL User → SQL APIKey+Plan → Rebuild Redis session
    API Key: SHA-256 → SQL APIKey → SQL User+Plan → Rebuild Redis session
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import Generator, Optional
from datetime import datetime, UTC

from src.db.session import SessionLocal
from src.db.models import User, APIKey, Plan, UserRole, UsageRecord, hash_api_key
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
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> UserSession:
    """
    Authenticate the current request via JWT OR API Key.

    Detection:
        - Token starts with "sk_" → API Key auth path
        - Anything else → JWT auth path

    Both paths produce the same UserSession dataclass with plan limits
    embedded, so the 6-tier rate limiter and all endpoints work identically.
    """
    if token.startswith("sk_"):
        return _auth_via_api_key(token, db, request)
    else:
        return _auth_via_jwt(token, db, request)


# ---------------------------------------------------------------------------
# JWT Auth Path
# ---------------------------------------------------------------------------

def _auth_via_jwt(
    token: str, db: Session, request: Request
) -> UserSession:
    """
    Standard JWT authentication.

    Fast path (Redis session warm) — zero SQL queries.
    Cold path (session missing) — one SQL round-trip to rebuild.
    """
    # ── 1. Blacklist check ───────────────────────────────────────────────
    if redis_service.is_blacklisted(token):
        redis_service.incr_gateway_stat("unauthorized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked/logged out."
        )

    # ── 2. Decode JWT ────────────────────────────────────────────────────
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

    # ── 3. Redis fast path ───────────────────────────────────────────────
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
            organization_id=int(session["organization_id"]) if session.get("organization_id") else None,
            api_key_hash=session.get("api_key_hash"),
        )

    # ── 4. SQL cold path — rebuild session ───────────────────────────────
    user, plan_name, rps, daily_quota, monthly_quota, org_id, api_key_hash_val = (
        _resolve_user_plan(user_id, db)
    )

    ttl = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    now = int(datetime.now(UTC).timestamp())

    redis_service.create_session(user.id, {
        "user_id":         user.id,
        "email":           user.email,
        "role":            user.role if isinstance(user.role, str) else user.role.value,
        "plan":            plan_name,
        "rps":             rps,
        "daily_quota":     daily_quota,
        "monthly_quota":   monthly_quota,
        "organization_id": org_id or "",
        "api_key_hash":    api_key_hash_val or "",
        "login_time":      now,
        "ip":              request.client.host if request.client else "unknown",
        "user_agent":      request.headers.get("user-agent", "unknown"),
        "expires":         now + ttl,
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
        organization_id=org_id,
        api_key_hash=api_key_hash_val,
    )


# ---------------------------------------------------------------------------
# API Key Auth Path
# ---------------------------------------------------------------------------

def _auth_via_api_key(
    raw_key: str, db: Session, request: Request
) -> UserSession:
    """
    API Key authentication (Bearer sk_...).

    Fast path: Redis session keyed by hash prefix.
    Cold path: SHA-256 hash → lookup api_keys table → resolve user + plan.
    """
    key_hash = hash_api_key(raw_key)
    hash_prefix = key_hash[:16]

    # ── Redis fast path ──────────────────────────────────────────────────
    session = redis_service.get_apikey_session(hash_prefix)
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
            organization_id=int(session["organization_id"]) if session.get("organization_id") else None,
            api_key_hash=hash_prefix,
        )

    # ── SQL cold path ────────────────────────────────────────────────────
    api_key_record = (
        db.query(APIKey)
        .filter(APIKey.key_hash == key_hash, APIKey.is_active == True)
        .first()
    )
    if not api_key_record:
        redis_service.incr_gateway_stat("unauthorized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key."
        )

    user = api_key_record.user
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this API key is inactive."
        )

    # Update last_used_at
    api_key_record.last_used_at = datetime.now(UTC)
    db.commit()

    # Resolve plan
    plan = api_key_record.plan_rel or db.query(Plan).filter(Plan.name == "free").first()
    plan_name     = plan.name              if plan else "free"
    rps           = plan.requests_per_sec  if plan else 5
    daily_quota   = plan.daily_quota       if plan else 1000
    monthly_quota = plan.monthly_quota     if plan else 30_000
    org_id        = user.organization_id

    # Build Redis session
    ttl = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    now = int(datetime.now(UTC).timestamp())

    redis_service.create_apikey_session(hash_prefix, {
        "user_id":         user.id,
        "email":           user.email,
        "role":            user.role if isinstance(user.role, str) else user.role.value,
        "plan":            plan_name,
        "rps":             rps,
        "daily_quota":     daily_quota,
        "monthly_quota":   monthly_quota,
        "organization_id": org_id or "",
        "api_key_hash":    hash_prefix,
        "login_time":      now,
        "ip":              request.client.host if request.client else "unknown",
        "user_agent":      request.headers.get("user-agent", "unknown"),
        "expires":         now + ttl,
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
        organization_id=org_id,
        api_key_hash=hash_prefix,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_user_plan(user_id: int, db: Session):
    """Load user + plan from SQL. Returns (user, plan_name, rps, daily, monthly, org_id, key_hash)."""
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
    org_id        = user.organization_id
    key_hash_val  = api_key_record.key_hash[:16] if api_key_record else None

    return user, plan_name, rps, daily_quota, monthly_quota, org_id, key_hash_val


def check_rate_limit(
    request: Request,
    user: UserSession = Depends(get_current_user),
) -> None:
    """
    6-tier rate limiting and quota enforcement.

    Delegates to the rate_limiter engine which checks:
        gateway → IP → organization → user → API key → model

    Then enforces daily and monthly quotas via Redis counters.
    """
    from src.core.rate_limiter import enforce_rate_limits

    ip_address = request.client.host if request.client else "127.0.0.1"

    # ── 6-tier sliding-window rate limits ─────────────────────────────────
    enforce_rate_limits(
        user_id=user.id,
        ip_address=ip_address,
        organization_id=getattr(user, "organization_id", None),
        api_key_hash=getattr(user, "api_key_hash", None),
        model=None,      # model-level check is done in chat.py after resolution
        rps=getattr(user, "rps", 5),
        daily_quota=getattr(user, "daily_quota", 1000),
        monthly_quota=getattr(user, "monthly_quota", 30_000),
    )

    # ── Daily quota (Redis INCR counter) ─────────────────────────────────
    today_str = datetime.now(UTC).date().isoformat()
    daily_quota = getattr(user, "daily_quota", 1000)
    current_daily = redis_service.get_quota(user.id, today_str)

    if current_daily >= daily_quota:
        redis_service.incr_gateway_stat("rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily quota exceeded",
            headers={"X-RateLimit-Tier": "daily_quota"},
        )

    # ── Monthly quota (sum Redis usage keys) ─────────────────────────────
    monthly_quota = getattr(user, "monthly_quota", 30_000)
    today = datetime.now(UTC).date()
    start_of_month = today.replace(day=1)
    monthly_used = _sum_monthly_usage_from_redis(user.id, start_of_month, today)

    if monthly_used >= monthly_quota:
        redis_service.incr_gateway_stat("rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Monthly quota exceeded. Allowed: {monthly_quota} requests/month.",
            headers={"X-RateLimit-Tier": "monthly_quota"},
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
