from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import datetime

from src.db.models import User, APIKey, UsageRecord, AuditLog, Plan
from src.core.deps import get_db, get_current_user, require_admin

router = APIRouter()

# --- Schemas ---

class UserMeResponse(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    plan: Optional[str] = None

    class Config:
        from_attributes = True

class UsageResponse(BaseModel):
    id: int
    date: str
    request_count: int
    input_tokens: int
    output_tokens: int
    cost: float

    class Config:
        from_attributes = True

class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    endpoint: str
    method: str
    status_code: int
    latency_ms: int
    timestamp: datetime.datetime

    class Config:
        from_attributes = True

class UserListResponse(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class PlanResponse(BaseModel):
    id: int
    name: str
    requests_per_sec: int
    daily_quota: int
    monthly_quota: int
    created_at: datetime.datetime

    class Config:
        from_attributes = True

# --- User Endpoints ---

@router.get("/me", response_model=UserMeResponse)
def get_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Retrieve details about the current active user."""
    api_key_record = db.query(APIKey).filter(APIKey.user_id == user.id, APIKey.is_active == True).first()
    plan_name = api_key_record.plan_rel.name if api_key_record and api_key_record.plan_rel else "free"
    
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "plan": plan_name
    }

@router.get("/usage", response_model=List[UsageResponse])
def get_usage(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Retrieve user-specific daily requests counts and token consumption statistics."""
    usages = db.query(UsageRecord).filter(UsageRecord.user_id == user.id).all()
    return usages

# --- Admin Protected Endpoints ---

@router.get("/audit-logs", response_model=List[AuditLogResponse])
def get_audit_logs(
    admin_user: User = Depends(require_admin), 
    db: Session = Depends(get_db)
):
    """[Admin Only] Retrieve all system audit logs."""
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).all()
    return logs

@router.get("/users", response_model=List[UserListResponse])
def list_users(
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """[Admin Only] Retrieve list of all users in the system."""
    users = db.query(User).order_by(User.id.asc()).all()
    return users

@router.get("/plans", response_model=List[PlanResponse])
def list_plans(
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """[Admin Only] Retrieve list of all rate limiting plans."""
    plans = db.query(Plan).order_by(Plan.id.asc()).all()
    return plans
