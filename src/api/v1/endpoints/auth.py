from fastapi import APIRouter, Depends, HTTPException, status
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
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """Authenticate email and password, returning JWT access and refresh tokens."""
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not security.verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = security.create_access_token(
        subject=user.id,
        email=user.email,
        role=user.role
    )
    refresh_token = security.create_refresh_token(subject=user.id)

    # Create Redis session (Phase 5)
    redis_service.set_session(user.id, {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
    })

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: TokenRefreshRequest, db: Session = Depends(get_db)):
    """Exchange a refresh token for new access and refresh tokens."""
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
        
    access_token = security.create_access_token(
        subject=user.id,
        email=user.email,
        role=user.role
    )
    new_refresh_token = security.create_refresh_token(subject=user.id)
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer"
    }

@router.post("/logout")
def logout(token: str = Depends(oauth2_scheme)):
    """Revoke the current JWT by blacklisting it with TTL = remaining token lifetime."""
    try:
        payload = pyjwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience="route-mobile-api",
            issuer="route-mobile",
        )
        exp_timestamp = payload.get("exp", 0)
        now = int(datetime.now(UTC).timestamp())
        remaining = max(exp_timestamp - now, 1)  # at least 1 second
    except Exception:
        remaining = 1800  # fallback: 30 min

    redis_service.blacklist_token(token, expires_in_sec=remaining)
    return {"message": "Successfully logged out"}

@router.get("/check")
def check_auth(token: str = Depends(oauth2_scheme)):
    """Verify if the current JWT is still valid (not blacklisted, not expired)."""
    # Check blacklist
    if redis_service.is_token_blacklisted(token):
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
