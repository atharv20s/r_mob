# Route Mobile — Enterprise AI Gateway

FastAPI backend with **real Redis-backed** rate limiting, response caching, JWT blacklisting, session management, and daily quota enforcement.

---

## ⚡ Production-Grade Architecture (Redis-First & Hybrid Hybrid Inference)

### System Topology Diagram

```text
                                  Internet
                                     │
                                     ▼
                      http://52.65.114.230:800 (Gateway)
                      http://52.65.114.230:3000 (Grafana)
                                     │
                         ┌───────────────────────┐
                         │       AWS EC2         │
                         │  Amazon Linux 2023    │
                         └───────────┬───────────┘
                                     │
                             Docker Compose
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
┌───────────────┐            ┌───────────────┐            ┌───────────────┐
│  FastAPI      │            │  PostgreSQL   │            │    Redis      │
│  AI Gateway   │◄──────────►│  16-Alpine    │            │   7-Alpine    │
│  (Port 8000)  │            │  (Port 5432)  │            │  (Port 6337)  │
│  - JWT Auth   │            │  - Users      │            │  - Cache      │
│  - Rate Limit │            │  - Orgs/Plans │            │  - Sessions   │
│  - OpenAI API │            │  - Audit Logs │            │  - Rate Limits│
│  - Metrics    │            │  - Invoices   │            │  - Telemetry  │
└───────┬───────┘            └───────────────┘            └───────────────┘
        │
        │ Scrapes Metrics /metrics
        ▼
┌───────────────┐            ┌───────────────┐
│  Prometheus   │───────────►│    Grafana    │
│  (Port 9090)  │            │  (Port 3000)  │
└───────────────┘            └───────────────┘
        │
        │ Provider Routing (InferenceRouter)
        ▼
┌──────────────────┐
│ Ollama Provider  │
└────────┬─────────┘
         │
    Tailscale VPN (Encrypted Mesh Network)
         │
         ▼ (http://100.127.226.10:11434)
┌─────────────────────────────────────────┐
│ Your Local Windows Machine / Laptop     │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │ Ollama Inference Engine           │  │
│  ├───────────────────────────────────┤  │
│  │  - qwen2.5:7b (Default Active)    │  │
│  │  - llama3:latest                  │  │
│  │  - gemma2:2b                      │  │
│  │  - glm4                           │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

### Request Hot-Path (0 SQL Queries for 95% of requests)

```
Client ──► JWT Verify ──► Redis Blacklist Check ──► Redis Session Look-up (Plan & Limits)
                                                        │
 Client ◄─── Rate Limit Exceeded (429) ◄── [ZSET Sliding Window]
                                                        │
 Client ◄─── Quota Exceeded (429) ◄───────── [Daily Quota INCR]
                                                        │
 Client ◄─── [Cache HIT] ◄───────────────── [Cache (Provider:Model:Hash)]
                                                        │ (Cache MISS)
                                                        ▼
 Client ◄─── Response ◄─── Audit Buffer ◄──── Tailscale Mesh ◄──── Ollama (Local GPU/CPU)
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

You can run the gateway in **Containerized Mode** (recommended, runs all components including the gateway inside Docker) or **Hybrid Mode** (runs gateway locally, dependencies in Docker).

---

### Option A: Fully Containerized Mode (Recommended)

This option runs the gateway and all dependencies inside Docker.

#### 1. Spin up the cluster
Start all services defined in `docker-compose.yml` (Gateway, PostgreSQL, Redis, 3x Ollama, Prometheus, Grafana):
```bash
docker compose up --build -d
```
Docker Compose will wait for the database, Redis, and Ollama nodes to report healthy before starting the gateway container.

#### 2. Seed Ollama Models
The three Ollama nodes start with empty data volumes. Run the helper script to pull the `gemma2:2b` model (configured in model pricing) into all three containers:

* **Windows (PowerShell)**:
  ```powershell
  .\scripts\pull_models.ps1
  ```
* **Linux / Git Bash (Shell)**:
  ```bash
  chmod +x ./scripts/pull_models.sh
  ./scripts/pull_models.sh
  ```

#### 3. Access the Gateway and Dashboard
Navigate to:
- **Interactive API Docs**: http://localhost:8000/docs
- **Gateway Portal / Dashboard**: http://localhost:8000/portal
- **Grafana Metrics**: http://localhost:3000 (Credentials: `admin` / `admin`)

---

### Option B: Hybrid Development Mode

Use this option if you want to run the gateway server directly on your host machine while running postgres and redis dependencies in Docker.

#### 1. Start Docker Dependencies (Redis + PostgreSQL)
Spin up PostgreSQL and Redis only:
```bash
docker compose up -d db redis-cluster
```

#### 2. Install Dependencies Locally
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

#### 3. Configure Local Environment (`.env`)
Create a `.env` file in the root directory:
```ini
# FastAPI Settings
PROJECT_NAME="Route Mobile API"
API_V1_STR="/api/v1"
DEBUG=true

# Database & Redis (Point to localhost mapped ports)
DATABASE_URL=sqlite:///./route_mobile.db
REDIS_URL=redis://localhost:6337
REDIS_REQUIRED=true

# Local Ollama URL (Assumes Ollama is running natively on your host machine)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_NODES=http://localhost:11434
DEFAULT_PROVIDER=ollama
DEFAULT_MODEL=gemma2:2b

# Security & JWT
JWT_SECRET=e42fa521f57912187bc9ba6f196bcf72e1ab5a7828de3e40776b6d51111956e1
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

#### 4. Run the Server
```bash
uvicorn src.main:app --reload
```

---

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
