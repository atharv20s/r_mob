from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import Generator
import jwt

from src.db.session import SessionLocal
from src.db.models import User, APIKey, Plan, UserRole
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
    """Dependency that applies Redis-based rate limiting (10 req/sec by default)."""
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
            detail=f"Rate limit exceeded. Allowed: {limit} req/sec."
        )

def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency that checks if the current authenticated user has admin privileges."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin role required."
        )
    return user
