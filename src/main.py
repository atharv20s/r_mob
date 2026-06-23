from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.core.config import settings
from src.api.v1.router import api_router
from src.core.security import get_password_hash

# Import database sessions and ORM models
from src.db.session import engine, Base, SessionLocal
from src.db import models

# Initialize and create database tables
Base.metadata.create_all(bind=engine)

# Seed database with the tiered plan limits and test users
db = SessionLocal()
try:
    # 1. Seed plans (free, pro, enterprise)
    plans = {
        "free": {"requests_per_sec": 5, "daily_quota": 100, "monthly_quota": 3000},
        "pro": {"requests_per_sec": 20, "daily_quota": 5000, "monthly_quota": 150000},
        "enterprise": {"requests_per_sec": 100, "daily_quota": 50000, "monthly_quota": 1500000}
    }
    for plan_name, specs in plans.items():
        plan_rec = db.query(models.Plan).filter(models.Plan.name == plan_name).first()
        if not plan_rec:
            plan_rec = models.Plan(
                name=plan_name,
                requests_per_sec=specs["requests_per_sec"],
                daily_quota=specs["daily_quota"],
                monthly_quota=specs["monthly_quota"]
            )
            db.add(plan_rec)
            db.commit()
            print(f"Database initialized: {plan_name} plan seeded.")

    # 2. Seed Admin User
    admin_user = db.query(models.User).filter(models.User.email == "admin@route.com").first()
    if not admin_user:
        admin_user = models.User(
            email="admin@route.com",
            password_hash=get_password_hash("adminpassword"),
            role="admin",
            is_active=True
        )
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        
        # Get enterprise plan
        ent_plan = db.query(models.Plan).filter(models.Plan.name == "enterprise").first()
        
        # Seed API key for admin
        admin_key = models.APIKey(
            user_id=admin_user.id,
            key_hash=models.hash_api_key("sk_admin_test_key_12345"),
            plan_id=ent_plan.id,
            is_active=True
        )
        db.add(admin_key)
        db.commit()
        print("Database initialized: Seeded admin@route.com with key: sk_admin_test_key_12345")

    # 3. Seed Normal User
    normal_user = db.query(models.User).filter(models.User.email == "user@route.com").first()
    if not normal_user:
        normal_user = models.User(
            email="user@route.com",
            password_hash=get_password_hash("userpassword"),
            role="user",
            is_active=True
        )
        db.add(normal_user)
        db.commit()
        db.refresh(normal_user)
        
        # Get free plan
        free_plan = db.query(models.Plan).filter(models.Plan.name == "free").first()
        
        # Seed API key for user
        user_key = models.APIKey(
            user_id=normal_user.id,
            key_hash=models.hash_api_key("sk_user_test_key_12345"),
            plan_id=free_plan.id,
            is_active=True
        )
        db.add(user_key)
        db.commit()
        print("Database initialized: Seeded user@route.com with key: sk_user_test_key_12345")

finally:
    db.close()

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Set CORS origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/", tags=["Health"])
def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}!",
        "status": "healthy",
        "docs": "/docs"
    }
