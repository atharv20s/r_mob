from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Generator
import jwt
from datetime import datetime, UTC

from src.db.session import SessionLocal
from src.db.models import User, APIKey, Plan, UserRole, UsageRecord
from src.core.config import settings
from src.core.security import decode_access_token
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
) -> User:
    """Dependency to extract the current authenticated user from a JWT Bearer token."""
    # 1. Verify token is not in the Redis logout blacklist
    if redis_service.is_token_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked/logged out."
        )

    # 2. Decode access token
    user_id_str = decode_access_token(token)
    if not user_id_str:
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
    return user

def check_rate_limit(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> None:
    """Dependency that applies Redis-based rate limiting and quota enforcement."""
    # Find user plan from API Key
    api_key_record = db.query(APIKey).filter(APIKey.user_id == user.id, APIKey.is_active == True).first()
    if api_key_record and api_key_record.plan_rel:
        plan_record = api_key_record.plan_rel
    else:
        plan_record = db.query(Plan).filter(Plan.name == "free").first()
    limit = plan_record.requests_per_sec if plan_record else 5  # Fallback to 5 req/s (free tier)

    # Rate limiting lookup using Redis service
    allowed = redis_service.is_allowed(user_id=str(user.id), limit=limit, window=1)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    # Daily Quota Enforcement via Redis
    today_str = datetime.now(UTC).date().isoformat()
    daily_quota = plan_record.daily_quota if plan_record else 1000
    current_usage = redis_service.get_quota(user.id, today_str)

    if current_usage >= daily_quota:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily quota exceeded"
        )

    # Monthly Quota Enforcement via SQLAlchemy (historical data)
    today = datetime.now(UTC).date()
    start_of_month = today.replace(day=1)

    monthly_usage = db.query(func.sum(UsageRecord.request_count)).filter(
        UsageRecord.user_id == user.id,
        UsageRecord.date >= start_of_month
    ).scalar() or 0

    monthly_quota = plan_record.monthly_quota if plan_record else 30000

    if monthly_usage >= monthly_quota:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Monthly quota exceeded. Allowed: {monthly_quota} requests/month."
        )

def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency that checks if the current authenticated user has admin privileges."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin role required."
        )
    return user
