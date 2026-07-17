"""
Organization Management API
=============================
CRUD + billing endpoints for multi-tenant organization management.

All endpoints require admin authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import List, Optional
import datetime

from src.db.models import User
from src.db.models_billing import Organization, Invoice, BillingEvent, InvoiceStatus
from src.core.deps import get_db, require_admin
from src.core.schemas import UserSession

router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────

class OrgCreateRequest(BaseModel):
    name: str
    slug: str
    max_users: int = 10

class OrgResponse(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    max_users: int
    user_count: int = 0
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class OrgDetailResponse(OrgResponse):
    users: List[dict] = []
    total_cost: float = 0.0

class InvoiceResponse(BaseModel):
    id: int
    organization_id: int
    period_start: datetime.date
    period_end: datetime.date
    total_amount: float
    currency: str
    status: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class AddUserRequest(BaseModel):
    user_id: int


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
def create_organization(
    payload: OrgCreateRequest,
    admin: UserSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Create a new organization."""
    existing = db.query(Organization).filter(Organization.slug == payload.slug).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization with slug '{payload.slug}' already exists.",
        )

    org = Organization(
        name=payload.name,
        slug=payload.slug,
        max_users=payload.max_users,
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_active=org.is_active,
        max_users=org.max_users,
        user_count=0,
        created_at=org.created_at,
    )


@router.get("/", response_model=List[OrgResponse])
def list_organizations(
    admin: UserSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """[Admin] List all organizations with user counts."""
    orgs = db.query(Organization).order_by(Organization.id.asc()).all()
    result = []
    for org in orgs:
        user_count = db.query(User).filter(User.organization_id == org.id).count()
        result.append(OrgResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            is_active=org.is_active,
            max_users=org.max_users,
            user_count=user_count,
            created_at=org.created_at,
        ))
    return result


@router.get("/{org_id}", response_model=OrgDetailResponse)
def get_organization(
    org_id: int,
    admin: UserSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Get organization details with users and cost summary."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")

    users = db.query(User).filter(User.organization_id == org_id).all()
    user_list = [
        {"id": u.id, "email": u.email, "role": u.role if isinstance(u.role, str) else u.role.value}
        for u in users
    ]

    total_cost = db.query(func.sum(BillingEvent.total_cost)).filter(
        BillingEvent.organization_id == org_id
    ).scalar() or 0.0

    return OrgDetailResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_active=org.is_active,
        max_users=org.max_users,
        user_count=len(user_list),
        created_at=org.created_at,
        users=user_list,
        total_cost=total_cost,
    )


@router.post("/{org_id}/users", status_code=status.HTTP_200_OK)
def add_user_to_org(
    org_id: int,
    payload: AddUserRequest,
    admin: UserSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Add a user to an organization."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")

    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Check max_users limit
    current_count = db.query(User).filter(User.organization_id == org_id).count()
    if current_count >= org.max_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Organization has reached its max user limit ({org.max_users}).",
        )

    user.organization_id = org_id
    db.commit()

    return {"detail": f"User {user.email} added to org '{org.name}'."}


@router.get("/{org_id}/usage")
def get_org_usage(
    org_id: int,
    admin: UserSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Get aggregated usage for an organization."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")

    from src.db.models import UsageRecord

    user_ids = [u.id for u in db.query(User.id).filter(User.organization_id == org_id).all()]

    if not user_ids:
        return {"organization": org.name, "total_requests": 0, "total_input_tokens": 0, "total_output_tokens": 0}

    agg = db.query(
        func.sum(UsageRecord.request_count),
        func.sum(UsageRecord.input_tokens),
        func.sum(UsageRecord.output_tokens),
    ).filter(UsageRecord.user_id.in_(user_ids)).first()

    return {
        "organization": org.name,
        "total_requests": agg[0] or 0,
        "total_input_tokens": agg[1] or 0,
        "total_output_tokens": agg[2] or 0,
        "user_count": len(user_ids),
    }


@router.get("/{org_id}/invoices", response_model=List[InvoiceResponse])
def get_org_invoices(
    org_id: int,
    admin: UserSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """[Admin] List invoices for an organization."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")

    invoices = (
        db.query(Invoice)
        .filter(Invoice.organization_id == org_id)
        .order_by(Invoice.period_start.desc())
        .all()
    )
    return [
        InvoiceResponse(
            id=inv.id,
            organization_id=inv.organization_id,
            period_start=inv.period_start,
            period_end=inv.period_end,
            total_amount=inv.total_amount,
            currency=inv.currency,
            status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
            created_at=inv.created_at,
        )
        for inv in invoices
    ]
