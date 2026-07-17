"""
src/core/security.py
====================
JWT and password utility functions.

Functions:
    verify_password       — bcrypt comparison
    get_password_hash     — bcrypt hashing
    create_access_token   — JWT access token with enterprise claims
    decode_access_token   — decode and validate access token
    create_refresh_token  — JWT refresh token (7-day default)
    decode_refresh_token  — decode and validate refresh token
"""

from datetime import datetime, timedelta, UTC
from typing import Any, Union, Optional
import logging
import jwt
import bcrypt
from src.core.config import settings

logger = logging.getLogger("security")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify standard plain text passwords against their bcrypt hashes."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception as e:
        logger.error("Password verification failed: %s", e)
        return False

def get_password_hash(password: str) -> str:
    """Generate bcrypt hash for storage."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def create_access_token(
    subject: Union[str, Any], 
    email: str, 
    role: str, 
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create a signed JWT access token with enterprise claims."""
    now = datetime.now(UTC)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "sub": str(subject),
        "email": email,
        "role": role,
        "iat": now,
        "exp": expire,
        "iss": "route-mobile",
        "aud": "route-mobile-api"
    }
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[str]:
    """Decode a JWT access token and return the subject (user_id)."""
    try:
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET, 
            algorithms=[settings.JWT_ALGORITHM],
            audience="route-mobile-api",
            issuer="route-mobile"
        )
        return payload.get("sub")
    except jwt.PyJWTError as e:
        logger.warning("Access token decode failed: %s", e)
        return None

def create_refresh_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT refresh token (7 days default)."""
    now = datetime.now(UTC)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=7)
    
    to_encode = {
        "sub": str(subject),
        "iat": now,
        "exp": expire,
        "iss": "route-mobile",
        "aud": "route-mobile-api",
        "typ": "refresh"
    }
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

def decode_refresh_token(token: str) -> Optional[str]:
    """Decode a JWT refresh token and return the subject (user_id)."""
    try:
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET, 
            algorithms=[settings.JWT_ALGORITHM],
            audience="route-mobile-api",
            issuer="route-mobile"
        )
        if payload.get("typ") != "refresh":
            return None
        return payload.get("sub")
    except jwt.PyJWTError as e:
        logger.warning("Refresh token decode failed: %s", e)
        return None
