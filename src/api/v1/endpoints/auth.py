from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import secrets
import jwt as pyjwt
from datetime import datetime, UTC
from pydantic import BaseModel, EmailStr

from src.db.models import User, APIKey, Plan, hash_api_key
from src.core import security
from src.core.config import settings
from src.core.deps import get_db, get_current_user, oauth2_scheme
from src.core.schemas import UserSession
from src.services.redis_service import redis_service

router = APIRouter()


class UserRegister(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    api_key: str  # raw key returned ONCE at registration

    class Config:
        from_attributes = True


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


@router.post("/register", response_model=UserResponse)
def register(user_in: UserRegister, db: Session = Depends(get_db)):
    """Register a new user account. The raw API key is returned only once."""
    user = db.query(User).filter(User.email == user_in.email).first()
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists."
        )

    new_user = User(
        email=user_in.email,
        password_hash=security.get_password_hash(user_in.password),
        role="user",
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Get free plan id for initial key
    free_plan = db.query(Plan).filter(Plan.name == "free").first()
    if not free_plan:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Free plan not seeded in the database."
        )

    # Generate API key — store only the SHA-256 hash
    raw_api_key = f"sk_{secrets.token_urlsafe(48)}"
    new_api_key = APIKey(
        user_id=new_user.id,
        key_hash=hash_api_key(raw_api_key),
        plan_id=free_plan.id,
        is_active=True
    )
    db.add(new_api_key)
    db.commit()

    return {
        "id": new_user.id,
        "email": new_user.email,
        "role": new_user.role,
        "is_active": new_user.is_active,
        "api_key": raw_api_key  # returned ONCE, never stored raw
    }


@router.post("/login", response_model=TokenResponse)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """Authenticate email and password, returning JWT access and refresh tokens.

    The Redis session is enriched with plan/quota data on login so that
    subsequent requests never need to query the Plan or APIKey tables.
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not security.verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Resolve plan (one SQL query paid once per session) ───────────────
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

    # ── Issue tokens ─────────────────────────────────────────────────────
    access_token  = security.create_access_token(
        subject=user.id, email=user.email, role=user.role
    )
    refresh_token = security.create_refresh_token(subject=user.id)

    # ── Build enriched Redis session ─────────────────────────────────────
    ip_addr    = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "unknown")
    login_time = int(datetime.now(UTC).timestamp())
    ttl        = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    redis_service.create_session(user.id, {
        "user_id":        user.id,
        "email":          user.email,
        "role":           user.role if isinstance(user.role, str) else user.role.value,
        "plan":           plan_name,
        "rps":            rps,
        "daily_quota":    daily_quota,
        "monthly_quota":  monthly_quota,
        "login_time":     login_time,
        "ip":             ip_addr,
        "user_agent":     user_agent,
        "expires":        login_time + ttl,
    }, ttl=ttl)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer"
    }


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: TokenRefreshRequest, db: Session = Depends(get_db)):
    """Exchange a refresh token for new access and refresh tokens.

    Rebuilds the Redis session so the new access token has a warm session
    immediately — no cold-path SQL on the first request after refresh.
    """
    user_id_str = security.decode_refresh_token(payload.refresh_token)
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token."
        )

    user_id = int(user_id_str)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive or not found."
        )

    # ── Resolve plan for rebuilt session ─────────────────────────────────
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

    access_token      = security.create_access_token(
        subject=user.id, email=user.email, role=user.role
    )
    new_refresh_token = security.create_refresh_token(subject=user.id)

    ttl  = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    now  = int(datetime.now(UTC).timestamp())

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

    return {
        "access_token":  access_token,
        "refresh_token": new_refresh_token,
        "token_type":    "bearer"
    }


@router.post("/logout")
def logout(
    token: str = Depends(oauth2_scheme),
    user: UserSession = Depends(get_current_user),
):
    """Revoke the current JWT by blacklisting it and deleting the Redis session."""
    try:
        decoded = pyjwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience="route-mobile-api",
            issuer="route-mobile",
        )
        exp_timestamp = decoded.get("exp", 0)
        now           = int(datetime.now(UTC).timestamp())
        remaining     = max(exp_timestamp - now, 1)  # at least 1 second
    except Exception:
        remaining = 1800  # fallback: 30 min

    # Blacklist the JWT so it cannot be reused
    redis_service.blacklist_token(token, expires_in_sec=remaining)

    # Also delete the session so the user_id cannot be reused from cache
    redis_service.delete_session(user.id)

    return {"message": "Successfully logged out"}


@router.get("/check")
def check_auth(token: str = Depends(oauth2_scheme)):
    """Verify if the current JWT is still valid (not blacklisted, not expired)."""
    # Check blacklist
    if redis_service.is_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked/logged out."
        )
    # Validate JWT
    user_id = security.decode_access_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token."
        )
    return {"status": "valid", "user_id": user_id}
