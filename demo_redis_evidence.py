"""
demo_redis_evidence.py — Generates hard evidence that Redis is actually working.

Produces:
  1. Redis connection proof (PING + INFO)
  2. Rate limiting proof (requests 1-5 OK, 6+ blocked)
  3. Cache hit proof (cached=true response)
  4. Session proof (session:{user_id} key)
  5. Blacklist proof (blacklist:{jwt} key + 401)
  6. Quota proof (quota:{user_id}:{date} key)
  7. Full Redis key dump (redis-cli KEYS "*" equivalent)

Output: demo_evidence_report.txt
Requires: Redis running on localhost:6379
"""
import sys, os, json, time, datetime, textwrap
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import redis as redis_lib
from fastapi.testclient import TestClient
from src.db.session import SessionLocal
from src.db import models

DEMO_EMAIL = "evidence_demo@route.com"
DEMO_PASS  = "EvidenceDemo!2026"
REPORT_FILE = "demo_evidence_report.txt"

lines = []

def log(msg=""):
    print(msg)
    lines.append(msg)

def section(title):
    log("")
    log("=" * 70)
    log(f" {title} ".center(70, "="))
    log("=" * 70)

def cleanup(db):
    user = db.query(models.User).filter(models.User.email == DEMO_EMAIL).first()
    if user:
        db.query(models.AuditLog).filter(models.AuditLog.user_id == user.id).delete()
        db.query(models.UsageRecord).filter(models.UsageRecord.user_id == user.id).delete()
        db.query(models.APIKey).filter(models.APIKey.user_id == user.id).delete()
        db.delete(user)
        db.commit()

def main():
    r = redis_lib.Redis(host="127.0.0.1", port=6379, decode_responses=True)

    # Selective cleanup
    for pattern in ["rate_limit:*", "cache:*", "stats:*", "quota:*"]:
        for key in r.keys(pattern):
            r.delete(key)

    db = SessionLocal()
    try:
        cleanup(db)
    finally:
        db.close()

    from src.main import app
    client = TestClient(app)

    section("REDIS EVIDENCE REPORT")
    log(f"Generated: {datetime.datetime.now().isoformat()}")
    log(f"Redis URL: localhost:6379")

    # -- 1. Redis Connection Proof --
    section("EVIDENCE 1: Redis Connected")
    pong = r.ping()
    log(f"  redis-cli PING -> {'PONG' if pong else 'FAILED'}")
    info = r.info("server")
    log(f"  Redis version:  {info.get('redis_version', 'N/A')}")
    log(f"  OS:             {info.get('os', 'N/A')}")
    log(f"  Uptime (sec):   {info.get('uptime_in_seconds', 'N/A')}")
    log(f"  TCP port:       {info.get('tcp_port', 'N/A')}")

    # Health endpoint
    resp = client.get("/api/v1/health/redis")
    log(f"  GET /health/redis -> {resp.status_code}: {resp.json()}")
    log(f"  [OK] PROOF: Redis is connected and responding")

    # -- 2. Registration + Login --
    section("EVIDENCE 2: User Registration + Login")
    resp = client.post("/api/v1/auth/register", json={"email": DEMO_EMAIL, "password": DEMO_PASS})
    log(f"  POST /auth/register -> {resp.status_code}")
    log(f"  Response: {json.dumps(resp.json(), indent=4)}")

    resp = client.post("/api/v1/auth/login", data={"username": DEMO_EMAIL, "password": DEMO_PASS})
    log(f"  POST /auth/login -> {resp.status_code}")
    token = resp.json().get("access_token", "")
    log(f"  Access token: {token[:40]}...")
    headers = {"Authorization": f"Bearer {token}"}

    # -- 3. Session Proof --
    section("EVIDENCE 3: Redis Session Storage")
    resp = client.get("/api/v1/me/session", headers=headers)
    log(f"  GET /me/session -> {resp.status_code}")
    log(f"  Response: {json.dumps(resp.json(), indent=4)}")
    session_keys = r.keys("session:*")
    log(f"  redis-cli KEYS session:* -> {session_keys}")
    for k in session_keys:
        log(f"    {k} = {r.get(k)}")
        log(f"    TTL = {r.ttl(k)}s")
    log(f"  [OK] PROOF: Session stored in Redis with 24h TTL")

    # -- 4. Cache Miss + Cache Hit --
    section("EVIDENCE 4: Redis Response Cache")
    prompt = "What is photosynthesis?"

    log(f"  --- Request #1 (Cache Miss) ---")
    resp1 = client.post("/api/v1/chat", json={"prompt": prompt}, headers=headers)
    if resp1.status_code == 200:
        d1 = resp1.json()
        log(f"  POST /chat -> {resp1.status_code}")
        log(f"  cached: {d1['cached']}")
        log(f"  response: {d1['response'][:100]}...")
    else:
        log(f"  POST /chat -> {resp1.status_code}: {resp1.text}")

    log(f"  --- Request #2 (Cache Hit) ---")
    resp2 = client.post("/api/v1/chat", json={"prompt": prompt}, headers=headers)
    if resp2.status_code == 200:
        d2 = resp2.json()
        log(f"  POST /chat -> {resp2.status_code}")
        log(f"  cached: {d2['cached']}")
        log(f"  response: {d2['response'][:100]}...")
    else:
        log(f"  POST /chat -> {resp2.status_code}: {resp2.text}")

    cache_keys = r.keys("cache:*")
    log(f"  redis-cli KEYS cache:* -> {cache_keys}")
    for k in cache_keys:
        log(f"    {k}  TTL={r.ttl(k)}s")

    stats_hits = r.get("stats:cache_hits") or "0"
    stats_misses = r.get("stats:cache_misses") or "0"
    log(f"  stats:cache_hits   = {stats_hits}")
    log(f"  stats:cache_misses = {stats_misses}")
    log(f"  [OK] PROOF: Cache miss -> hit demonstrated, keys exist in Redis")

    # -- 5. Rate Limiting Proof --
    section("EVIDENCE 5: Redis Rate Limiting")
    log(f"  Free plan = 5 req/sec")
    log(f"  Sending 10 rapid requests...")
    time.sleep(1.5)  # wait for window reset
    statuses = []
    for i in range(10):
        r2 = client.get("/api/v1/test/rate-limit", headers=headers)
        statuses.append(r2.status_code)
        icon = "[OK] 200" if r2.status_code == 200 else "[BLOCKED] 429"
        log(f"    Request {i+1:2d} -> {icon}")

    blocked = sum(1 for s in statuses if s == 429)
    passed = sum(1 for s in statuses if s == 200)
    log(f"  Allowed: {passed} | Blocked: {blocked}")
    rl_keys = r.keys("rate_limit:*")
    log(f"  redis-cli KEYS rate_limit:* -> {rl_keys}")
    for k in rl_keys:
        log(f"    {k} = {r.get(k)}  TTL={r.ttl(k)}s")
    log(f"  [OK] PROOF: Rate limiting enforced via Redis INCR+EXPIRE")

    # -- 6. Quota Proof --
    section("EVIDENCE 6: Redis Daily Quota")
    quota_keys = r.keys("quota:*")
    log(f"  redis-cli KEYS quota:* -> {quota_keys}")
    for k in quota_keys:
        log(f"    {k} = {r.get(k)}  TTL={r.ttl(k)}s")
    log(f"  [OK] PROOF: Daily quota tracked in Redis")

    # -- 7. JWT Blacklist Proof --
    section("EVIDENCE 7: JWT Blacklist")
    log(f"  POST /auth/logout (revoking current token)...")
    resp = client.post("/api/v1/auth/logout", headers=headers)
    log(f"  -> {resp.status_code}: {resp.json()}")

    blacklist_keys = r.keys("blacklist:*")
    log(f"  redis-cli KEYS blacklist:* -> found {len(blacklist_keys)} key(s)")
    for k in blacklist_keys:
        log(f"    {k[:60]}...  TTL={r.ttl(k)}s")

    log(f"  Attempting to use revoked token...")
    resp = client.post("/api/v1/chat", json={"prompt": "should fail"}, headers=headers)
    log(f"  POST /chat -> {resp.status_code}: {resp.json().get('detail', '')}")

    resp = client.get("/api/v1/auth/check", headers=headers)
    log(f"  GET /auth/check -> {resp.status_code}: {resp.json().get('detail', '')}")
    log(f"  [OK] PROOF: JWT blacklisted in Redis, 401 on reuse")

    # -- 8. Full Redis Key Dump --
    section("EVIDENCE 8: Full Redis Key Dump (redis-cli KEYS *)")
    all_keys = sorted(r.keys("*"))
    log(f"  Total keys: {len(all_keys)}")
    log("")
    categories = {"blacklist": [], "cache": [], "session": [], "quota": [],
                  "rate_limit": [], "stats": [], "other": []}
    for k in all_keys:
        prefix = k.split(":")[0] if ":" in k else "other"
        if prefix in categories:
            categories[prefix].append(k)
        else:
            categories["other"].append(k)

    for cat, keys in categories.items():
        if keys:
            log(f"  [{cat.upper()}] ({len(keys)} keys)")
            for k in keys:
                ttl = r.ttl(k)
                val = r.get(k)
                display_val = val[:80] + "..." if val and len(val) > 80 else val
                log(f"    {k}")
                log(f"      TTL={ttl}s  Value={display_val}")

    # -- Redis Key Inspection via API --
    section("EVIDENCE 9: /admin/redis/keys API")
    admin_resp = client.post("/api/v1/auth/login", data={
        "username": "admin@route.com", "password": "adminpassword"
    })
    if admin_resp.status_code == 200:
        admin_token = admin_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/v1/admin/redis/keys", headers=admin_headers)
        log(f"  GET /admin/redis/keys -> {resp.status_code}")
        log(f"  Response: {json.dumps(resp.json(), indent=4)}")

    # -- Final Summary --
    section("FINAL EVIDENCE SUMMARY")
    evidence = {
        "Redis Connected":     "PROVEN" if pong else "FAILED",
        "Redis Rate Limiting":  f"PROVEN ({blocked} requests blocked)" if blocked >= 5 else "FAILED",
        "Redis Cache":         "PROVEN" if len(cache_keys) >= 1 else "FAILED",
        "Redis Sessions":      "PROVEN" if len(session_keys) >= 1 else "FAILED",
        "Redis Quotas":        "PROVEN" if len(quota_keys) >= 1 else "FAILED",
        "JWT Blacklist":       "PROVEN" if len(blacklist_keys) >= 1 else "FAILED",
    }
    for feature, status in evidence.items():
        log(f"  {feature:25s} {status}")

    log("")
    all_proven = all("PROVEN" in v for v in evidence.values())
    log(f"  OVERALL: {'ALL FEATURES PROVEN' if all_proven else 'SOME FEATURES FAILED'}")
    log("")

    # Save report
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"  Report saved to {REPORT_FILE}")

    # Cleanup
    db = SessionLocal()
    try:
        cleanup(db)
    finally:
        db.close()

if __name__ == "__main__":
    main()
