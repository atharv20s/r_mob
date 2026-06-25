# Route Mobile — Enterprise AI Gateway

FastAPI backend with **real Redis-backed** rate limiting, response caching, JWT blacklisting, session management, and daily quota enforcement.

## Architecture

```
Client → FastAPI → Redis (Rate Limit + Cache + Sessions + Quotas + JWT Blacklist) → Mistral AI
                 → SQLAlchemy (Audit Logs + Usage Records + Plans + Users)
```

## Features

- **Authentication**: JWT access + refresh tokens with Redis-backed blacklist on logout
- **Redis Rate Limiting**: Per-plan limits enforced via `INCR + EXPIRE` (Free: 5/s, Pro: 10/s, Enterprise: 50/s)
- **Redis Response Cache**: SHA-256 keyed, 600s TTL, hit/miss tracking
- **Redis Sessions**: User session stored on login (24h TTL)
- **Redis Daily Quotas**: Per-user per-day counters (Free: 1000, Pro: 10000, Enterprise: 100000)
- **Audit Logs**: Every request logged to SQLAlchemy with latency, IP, user agent
- **Mistral AI**: Async chat completions with retry + backoff

## Prerequisites

- Python 3.10+
- Docker Desktop (for Redis)
- Git

## Quick Start

### 1. Start Redis (REQUIRED)

```bash
docker compose up -d redis
```

Verify Redis is running:

```bash
docker exec -it route_mobile-redis-1 redis-cli ping
# Expected: PONG
```

### 2. Install Dependencies

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 3. Configure Environment

Create a `.env` file in the root directory with the following structure:

```ini
# FastAPI Settings
PROJECT_NAME="Route Mobile API"
API_V1_STR="/api/v1"
DEBUG=true

# AWS Configuration
AWS_ACCESS_KEY_ID=placeholder_key
AWS_SECRET_ACCESS_KEY=placeholder_secret
AWS_REGION=us-east-1
AWS_BUCKET_NAME=route-mobile-bucket

# AI Configuration (Gemini / OpenAI / Mistral)
GEMINI_API_KEY=placeholder_gemini_key
OPENAI_API_KEY=placeholder_openai_key
DEFAULT_MODEL=gemini-1.5-flash
MISTRAL_API_KEY=your_mistral_api_key

# Database & Redis Settings
DATABASE_URL=sqlite:///./route_mobile.db
REDIS_URL=redis://127.0.0.1:6379
REDIS_REQUIRED=true

# JWT Auth Settings
JWT_SECRET=your_random_256_bit_secret_here
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

Edit the `.env` with your API keys and configuration settings.

### 4. Run the Server

```bash
uvicorn src.main:app --reload
```

### 5. Open Docs

Navigate to http://127.0.0.1:8000/docs

## Redis Verification

### Health Check

```bash
curl http://localhost:8000/api/v1/health/redis
# {"status":"healthy","redis":"connected"}
```

### Inspect Redis Keys (Admin)

```bash
# Via API (requires admin JWT)
curl -H "Authorization: Bearer <admin_token>" http://localhost:8000/api/v1/admin/redis/keys

# Via redis-cli
docker exec -it route_mobile-redis-1 redis-cli KEYS "*"
```

Expected key patterns:
```
blacklist:{jwt_token}
cache:{sha256_hash}
rate_limit:{user_id}
session:{user_id}
quota:{user_id}:{date}
stats:cache_hits
stats:cache_misses
```

## Running Tests

### Full Test Suite

```bash
python test_redis_enterprise.py
```

### Rate Limit Tests (explicit assertions)

```bash
python test_rate_limit.py
```

### Cache Tests (explicit assertions)

```bash
python test_cache.py
```

### Evidence Generation

```bash
python demo_redis_evidence.py
# Outputs: demo_evidence_report.txt

python generate_validation_report.py
# Outputs: redis_validation_report.md
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register new user |
| POST | `/api/v1/auth/login` | Login (returns JWT) |
| POST | `/api/v1/auth/logout` | Logout (blacklists JWT) |
| POST | `/api/v1/auth/refresh` | Refresh tokens |
| GET  | `/api/v1/auth/check` | Verify token validity |
| POST | `/api/v1/chat` | Chat completion (cached) |
| GET  | `/api/v1/usage/me` | Current user info |
| GET  | `/api/v1/usage/usage` | Usage statistics |
| GET  | `/api/v1/me/session` | Redis session data |
| GET  | `/api/v1/health/redis` | Redis health check |
| GET  | `/api/v1/admin/redis/rate-limit` | Active rate limit keys |
| GET  | `/api/v1/admin/redis/keys` | All Redis key counts |
| GET  | `/api/v1/admin/cache/stats` | Cache hit/miss stats |

## Postman

Import both files into Postman:
- `postman_collection.json` — All API requests
- `postman_environment.json` — Variables (base_url, access_token, admin_token)

## Docker Compose (Full Stack)

```bash
docker compose up -d
```

Services: `web` (FastAPI), `db` (PostgreSQL), `redis` (Redis 7 Alpine)
