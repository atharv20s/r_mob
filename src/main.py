import uuid
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from src.core.config import settings
from src.core.logging_config import setup_logging
from src.api.v1.router import api_router
from src.core.security import get_password_hash

# Import database sessions and ORM models
from src.db.session import engine, Base, SessionLocal
from src.db import models
from src.db import models_billing

# ─── Configure structured logging FIRST ──────────────────────────────────────
setup_logging(
    level=settings.LOG_LEVEL,
    json_format=(settings.LOG_FORMAT == "json"),
)
logger = logging.getLogger("gateway")

# Initialize and create database tables
Base.metadata.create_all(bind=engine)

# Seed database with the tiered plan limits and test users
db = SessionLocal()
try:
    # 1. Seed plans (free, pro, enterprise) — values match enterprise spec
    plans = {
        "free": {"requests_per_sec": 5, "daily_quota": 1000, "monthly_quota": 30000},
        "pro": {"requests_per_sec": 10, "daily_quota": 10000, "monthly_quota": 300000},
        "enterprise": {"requests_per_sec": 50, "daily_quota": 100000, "monthly_quota": 3000000}
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
            logger.info("Database initialized: %s plan seeded.", plan_name)
        else:
            # Update existing plan to match spec
            plan_rec.requests_per_sec = specs["requests_per_sec"]
            plan_rec.daily_quota = specs["daily_quota"]
            plan_rec.monthly_quota = specs["monthly_quota"]
            db.commit()

    # 1b. Seed default "Personal" organization
    default_org = db.query(models_billing.Organization).filter(
        models_billing.Organization.slug == "personal"
    ).first()
    if not default_org:
        default_org = models_billing.Organization(
            name="Personal",
            slug="personal",
            is_active=True,
            max_users=1,
        )
        db.add(default_org)
        db.commit()
        db.refresh(default_org)
        logger.info("Database initialized: Default 'Personal' organization seeded (id=%d).", default_org.id)

    # 1c. Seed model pricing (baseline rates)
    pricing_defaults = [
        {"provider": "ollama",  "model": "gemma2:2b",             "input": 0.0,  "output": 0.0},
        {"provider": "mistral", "model": "mistral-large-latest",  "input": 2.0,  "output": 6.0},
        {"provider": "openai",  "model": "gpt-4o",                "input": 2.5,  "output": 10.0},
        {"provider": "gemini",  "model": "gemini-1.5-flash",      "input": 0.075, "output": 0.30},
    ]
    import datetime as _dt
    for p in pricing_defaults:
        existing = db.query(models_billing.ModelPricing).filter(
            models_billing.ModelPricing.provider == p["provider"],
            models_billing.ModelPricing.model == p["model"],
            models_billing.ModelPricing.is_active == True,
        ).first()
        if not existing:
            db.add(models_billing.ModelPricing(
                provider=p["provider"],
                model=p["model"],
                input_cost_per_1k=p["input"],
                output_cost_per_1k=p["output"],
                effective_from=_dt.date.today(),
                is_active=True,
            ))
    db.commit()
    logger.info("Database initialized: Model pricing seeded.")

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
        logger.info("Database initialized: Seeded admin@route.com with key: sk_admin_test_key_12345")

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
        logger.info("Database initialized: Seeded user@route.com with key: sk_user_test_key_12345")

finally:
    db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle.

    On startup:
        1. Validates Redis connection.
        2. Runs initial cluster health check (all inference nodes).
        3. Starts the background usage flusher (Redis → SQL every 60 s).
        4. Starts the background health check loop (every 30 s).

    On shutdown:
        1. Cancels both background tasks.
    """
    import asyncio
    from src.services.redis_service import redis_service
    from src.services.usage_flusher import usage_flush_loop
    from src.services.ai.cluster import health_check_loop, inference_cluster

    # ── Redis health check ───────────────────────────────────────────────
    if not redis_service.ping():
        if settings.REDIS_REQUIRED:
            raise RuntimeError(
                "[ERROR] Redis is required but not reachable. "
                "Set REDIS_REQUIRED=false to allow startup without Redis."
            )
        logger.warning("Redis unavailable at startup — running in degraded mode.")
    else:
        logger.info("Redis health check passed at startup.")

    # ── Initial cluster health check postponed to background task ───────

    # ── Start background tasks ───────────────────────────────────────────
    flusher_task = asyncio.create_task(usage_flush_loop())
    logger.info("Background usage flusher started.")

    health_task = asyncio.create_task(health_check_loop())
    logger.info("Background health check loop started (interval=%ds).", settings.HEALTH_CHECK_INTERVAL)

    yield

    # Graceful shutdown — cancel both tasks
    for task, name in [(flusher_task, "flusher"), (health_task, "health_check")]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("%s stopped cleanly.", name)


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── Mount Prometheus metrics endpoint ───────────────────────────────────────
if settings.METRICS_ENABLED:
    try:
        from src.core.metrics import create_metrics_app
        metrics_app = create_metrics_app()
        app.mount("/metrics", metrics_app)
        logger.info("Prometheus metrics mounted at /metrics")
    except ImportError:
        logger.warning("prometheus-client not installed — /metrics endpoint disabled")

# Set CORS origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    correlation_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = correlation_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = correlation_id
    return response

from src.api.v1.endpoints import openai_compat
app.include_router(openai_compat.router, prefix="/v1", tags=["OpenAI Compatible"])
app.include_router(api_router, prefix=settings.API_V1_STR)

# Mount static frontend portal
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    app.mount("/portal", StaticFiles(directory=static_dir, html=True), name="portal")

@app.get("/", tags=["Health"])
def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}!",
        "status": "healthy",
        "default_provider": settings.DEFAULT_PROVIDER,
        "default_model": settings.DEFAULT_MODEL,
        "docs": "/docs",
        "metrics": "/metrics",
        "portal": "/portal"
    }
