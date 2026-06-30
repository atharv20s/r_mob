# Route Mobile — Enterprise AI Gateway

FastAPI backend with **real Redis-backed** rate limiting, response caching, JWT blacklisting, session management, and daily quota enforcement.

---

## ⚡ Production-Grade Architecture (Redis-First)

The architecture is optimized to keep SQL databases out of the request hot-path, achieving low latency and horizontal scalability.

```
Request Hot-Path (0 SQL Queries for 95% of requests):
Client ──► JWT Verify ──► Redis Blacklist Check ──► Redis Session Look-up (Plan & Limits)
                                                        │
 Client ◄─── Rate Limit Exceeded (429) ◄── [ZSET Sliding Window]
                                                        │
 Client ◄─── Quota Exceeded (429) ◄───────── [Daily Quota INCR]
                                                        │
 Client ◄─── [Cache HIT] ◄───────────────── [Cache (Provider:Model:Hash)]
                                                        │ (Cache MISS)
                                                        ▼
 Client ◄─── Response ◄─── Audit Buffer ◄──── Mistral AI (Async Completion)
```

```
Background Synchronization Loop (60s tick):
[Redis usage:* counters] ──► [usage_flusher.py] ──► [UsageRecord SQL Table] (Upsert)
[Redis audit:buffer LIST] ──► [usage_flusher.py] ──► [AuditLog SQL Table] (Bulk-Insert)
```

---

## 🌟 Key Features

- **0-SQL Hot Path**: User plan parameters (`rps`, `daily_quota`, `monthly_quota`) are cached directly inside the Redis Session Hash on login/refresh. Requests read limits and enforce quotas entirely via Redis.
- **Batched Database Synchronization**:
  - **Usage Counter Aggregation**: Per-request token and request counts increment atomically inside a Redis Hash (`usage:{user_id}:{date}`) and are flushed to SQLite/Postgres once per minute.
  - **Buffered Audit Logs**: API audits are pushed to a Redis List (`audit:buffer`) and bulk-inserted into the database every 60 seconds.
- **Richer Cache Keys**: Cached responses are uniquely identified by a SHA-256 hash of the `prompt`, `temperature`, `system_prompt`, and `top_p` scoped under `cache:{provider}:{model}:{hash}` to avoid configuration collisions.
- **Conversation Inactivity TTL**: Conversation context `LIST` keys expire automatically after 1 hour of inactivity, protecting Redis memory from unbounded growth.
- **Sliding-Window Rate Limiting**: Enforced using Sorted Sets (`ZSET`) to ensure strict rate compliance over standard fixed windows.
- **Gateway Observability**: A dedicated `stats:gateway` Hash tracks requests, cache hits, misses, rate limits, errors, and average LLM latency in real time.

---

## 🛠️ Prerequisites

- Python 3.10+
- Docker Desktop (for Redis)
- Git

---

## 🚀 Quick Start

### 1. Start Redis

```bash
docker compose up -d redis
```

Verify connection:
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

Create a `.env` file in the root directory:

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

### 4. Run the Server

```bash
uvicorn src.main:app --reload
```

The background usage+audit flusher task starts automatically on application startup.

### 5. Open Interactive Documentation

Navigate to http://127.0.0.1:8000/docs or open the local dashboard at http://127.0.0.1:8000/portal.

---

## 📊 Redis Key Schemas

Verify keys inside Redis:
```bash
docker exec -it route_mobile-redis-1 redis-cli
```

| Key Pattern | Data Structure | Purpose | TTL |
|---|---|---|---|
| `session:{user_id}` | `HASH` | User profile + plan limits | 30 minutes |
| `blacklist:{jwt_token}` | `STRING` | Revoked access tokens | Remaining lifetime |
| `cache:{provider}:{model}:{sha256}` | `STRING` | Cached LLM responses | 10 minutes |
| `context:{user_id}` | `LIST` | Conversation message history (Max 40) | 1 hour |
| `rate_limit_slide:{user_id}` | `ZSET` | Sliding-window timestamps | 6 seconds |
| `quota:{user_id}:{date}` | `STRING` | Daily usage quota counter | 48 hours |
| `usage:{user_id}:{date}` | `HASH` | Aggregated requests and tokens | 48 hours |
| `audit:buffer` | `LIST` | Buffered audit records | Drained every 60s |
| `stats:gateway` | `HASH` | Global gateway traffic metrics | Indefinite |

---

## 📈 Monitoring & Admin API Endpoints

All admin endpoints require an Authorization Header with an Admin JWT.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/admin/redis/gateway-stats` | Live traffic metrics (hits, misses, latency, errors) |
| GET | `/api/v1/admin/redis/audit-buffer` | Pending audit queue depth |
| GET | `/api/v1/admin/redis/inspect` | Full Redis key inspector (for portal dashboard) |
| GET | `/api/v1/admin/redis/keys` | Count of active keys by prefix |
| GET | `/api/v1/admin/cache/stats` | Cache hit/miss/ratio stats |
| GET | `/api/v1/admin/redis/info` | Redis engine version, memory, uptime |
| DELETE | `/api/v1/admin/redis/flush` | Flush all keys in DB (Development only) |

---

## 🧪 Testing

Run tests to verify correct Redis behavior, rate limiting, and caching policies:

```bash
# Run comprehensive rate limit validations
python test_rate_limit.py

# Run response cache validations
python test_cache.py

# Run full project validations and print results
python test_redis_enterprise.py
```
