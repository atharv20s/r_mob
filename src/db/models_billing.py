"""
Enterprise Billing & Organization Models
==========================================
Multi-tenancy hierarchy:

    Organization
        └── Users (many)
            └── API Keys (many, each tied to a Plan)
                └── BillingEvents (per-request cost tracking)
        └── Invoices (monthly billing)

    ModelPricing (global — cost rates per provider+model)

These tables live in PostgreSQL alongside Users, Plans, and AuditLogs.
They store data that matters forever — billing, invoices, org membership.
"""

import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float,
    ForeignKey, Date, Enum as SqlEnum, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from src.db.session import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class InvoiceStatus(str, PyEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class BillingEventType(str, PyEnum):
    API_CALL = "api_call"
    TOKEN_USAGE = "token_usage"
    CACHE_HIT = "cache_hit"


# ---------------------------------------------------------------------------
# Organization — multi-tenancy root
# ---------------------------------------------------------------------------

class Organization(Base):
    """
    Top-level tenant entity.

    Every user belongs to exactly one organization.  New users are assigned
    to the default "Personal" organization on registration.  Enterprise
    customers get their own org with aggregated billing and usage limits.
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(128), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    max_users = Column(Integer, default=10)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    # Relationships
    users = relationship("User", back_populates="organization")
    invoices = relationship("Invoice", back_populates="organization", cascade="all, delete-orphan")
    billing_events = relationship("BillingEvent", back_populates="organization", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Invoice — monthly billing per organization
# ---------------------------------------------------------------------------

class Invoice(Base):
    """
    Monthly invoice generated for each organization.

    Aggregates all BillingEvents within the billing period into a single
    total_amount.  Status transitions: draft → issued → paid.
    """
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    total_amount = Column(Float, default=0.0)
    currency = Column(String(3), default="USD")
    status = Column(
        SqlEnum(InvoiceStatus),
        default=InvoiceStatus.DRAFT,
        nullable=False,
    )
    issued_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    # Unique constraint: one invoice per org per period
    __table_args__ = (
        UniqueConstraint("organization_id", "period_start", "period_end", name="uq_org_period"),
    )

    # Relationships
    organization = relationship("Organization", back_populates="invoices")


# ---------------------------------------------------------------------------
# BillingEvent — per-request cost tracking
# ---------------------------------------------------------------------------

class BillingEvent(Base):
    """
    Granular billing record for each chargeable event.

    Written by the usage flusher (not per-request) to keep the SQL write
    rate low.  Each event captures the provider, model, token counts,
    and computed cost using ModelPricing rates.
    """
    __tablename__ = "billing_events"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_type = Column(
        SqlEnum(BillingEventType),
        default=BillingEventType.API_CALL,
        nullable=False,
    )
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    unit_cost = Column(Float, default=0.0)       # cost per 1k tokens at time of event
    total_cost = Column(Float, default=0.0)       # computed cost for this event
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    # Relationships
    organization = relationship("Organization", back_populates="billing_events")
    user = relationship("User", backref="billing_events")


# ---------------------------------------------------------------------------
# ModelPricing — per-model cost rates
# ---------------------------------------------------------------------------

class ModelPricing(Base):
    """
    Cost rates per provider+model combination.

    Used to compute BillingEvent.total_cost from token counts.
    Supports versioning via effective_from so pricing changes don't
    retroactively affect past invoices.
    """
    __tablename__ = "model_pricing"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    input_cost_per_1k = Column(Float, default=0.0)    # $ per 1,000 input tokens
    output_cost_per_1k = Column(Float, default=0.0)   # $ per 1,000 output tokens
    effective_from = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    # Unique constraint: one active pricing per provider+model
    __table_args__ = (
        UniqueConstraint("provider", "model", "effective_from", name="uq_pricing_version"),
    )
