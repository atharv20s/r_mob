"""
generate_validation_report.py — Phase 10: Redis Validation Report.

Generates a comprehensive markdown validation report with:
  1. Redis connection proof
  2. Rate limiting proof
  3. Cache hit proof
  4. Session proof
  5. Blacklist proof
  6. Quota proof
  7. Redis key dump
  8. API endpoint results

Output: redis_validation_report.md
Requires: Redis running on localhost:6379
"""
import sys, os, json, time, datetime
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

DEMO_EMAIL = "validation_report@route.com"
DEMO_PASS  = "ValidationReport!2026"
REPORT_FILE = "redis_validation_report.md"

evidence = {}

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

    md = []
    md.append("# Redis Validation Report")
    md.append("")
    md.append(f"**Generated:** {datetime.datetime.now().isoformat()}")
    md.append(f"**Redis URL:** localhost:6379")
    md.append("")

    # -- 1. Connection --
    md.append("## 1. Redis Connection Proof")
    md.append("")
    pong = r.ping()
    info = r.info("server")
    md.append(f"| Check | Result |")
    md.append(f"|-------|--------|")
    md.append(f"| PING | `{'PONG ✅' if pong else 'FAILED ❌'}` |")
    md.append(f"| Redis Version | `{info.get('redis_version', 'N/A')}` |")
    md.append(f"| Uptime | `{info.get('uptime_in_seconds', 'N/A')}s` |")

    resp = client.get("/api/v1/health/redis")
    md.append(f"| GET /health/redis | `{resp.status_code}` → `{resp.json()}` |")
    md.append("")
    evidence["Redis Connected"] = pong

    # -- 2. Registration + Login --
    resp = client.post("/api/v1/auth/register", json={"email": DEMO_EMAIL, "password": DEMO_PASS})
    resp = client.post("/api/v1/auth/login", data={"username": DEMO_EMAIL, "password": DEMO_PASS})
    token = resp.json().get("access_token", "")
    headers = {"Authorization": f"Bearer {token}"}

    # Admin login
    admin_resp = client.post("/api/v1/auth/login", data={"username": "admin@route.com", "password": "adminpassword"})
    admin_token = admin_resp.json().get("access_token", "")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # -- 3. Session --
    md.append("## 2. Redis Session Proof")
    md.append("")
    resp = client.get("/api/v1/me/session", headers=headers)
    session_data = resp.json()
    session_keys = r.keys("session:*")
    md.append(f"| Check | Result |")
    md.append(f"|-------|--------|")
    md.append(f"| GET /me/session | `{resp.status_code}` |")
    md.append(f"| Session Data | `{json.dumps(session_data)}` |")
    md.append(f"| Redis Keys | `{session_keys}` |")
    for k in session_keys:
        md.append(f"| {k} TTL | `{r.ttl(k)}s` |")
    md.append("")
    evidence["Redis Sessions"] = len(session_keys) >= 1

    # -- 4. Cache --
    md.append("## 3. Redis Cache Proof")
    md.append("")
    prompt = "What is DNA replication?"
    resp1 = client.post("/api/v1/chat", json={"prompt": prompt}, headers=headers)
    resp2 = client.post("/api/v1/chat", json={"prompt": prompt}, headers=headers)

    d1 = resp1.json() if resp1.status_code == 200 else {}
    d2 = resp2.json() if resp2.status_code == 200 else {}

    cache_keys = r.keys("cache:*")
    md.append(f"| Check | Result |")
    md.append(f"|-------|--------|")
    md.append(f"| Request #1 cached | `{d1.get('cached', 'N/A')}` |")
    md.append(f"| Request #2 cached | `{d2.get('cached', 'N/A')}` |")
    md.append(f"| Cache Keys Count | `{len(cache_keys)}` |")
    md.append(f"| stats:cache_hits | `{r.get('stats:cache_hits') or 0}` |")
    md.append(f"| stats:cache_misses | `{r.get('stats:cache_misses') or 0}` |")
    md.append("")

    # Cache stats via API
    stats_resp = client.get("/api/v1/admin/cache/stats", headers=admin_headers)
    if stats_resp.status_code == 200:
        stats = stats_resp.json()
        md.append(f"**Cache Stats API:** hits={stats['hits']}, misses={stats['misses']}, ratio={stats['hit_ratio']}")
        md.append("")
    evidence["Redis Cache"] = d1.get("cached") == False and d2.get("cached") == True

    # -- 5. Rate Limiting --
    md.append("## 4. Redis Rate Limiting Proof")
    md.append("")
    md.append("Free plan = 5 req/sec. Sending 10 rapid requests:")
    md.append("")
    time.sleep(1.5)
    statuses = []
    for i in range(10):
        r2 = client.get("/api/v1/test/rate-limit", headers=headers)
        statuses.append(r2.status_code)

    md.append("| Request | Status |")
    md.append("|---------|--------|")
    for i, s in enumerate(statuses):
        icon = "✅" if s == 200 else "🚫"
        md.append(f"| {i+1} | `{s}` {icon} |")
    md.append("")

    blocked = sum(1 for s in statuses if s == 429)
    allowed = sum(1 for s in statuses if s == 200)
    rl_keys = r.keys("rate_limit:*")
    md.append(f"**Allowed:** {allowed} | **Blocked:** {blocked}")
    md.append(f"**Redis rate_limit keys:** `{rl_keys}`")
    md.append("")
    evidence["Redis Rate Limiting"] = blocked >= 5

    # Rate limit metrics
    resp = client.get("/api/v1/admin/redis/rate-limit", headers=admin_headers)
    if resp.status_code == 200:
        md.append(f"**GET /admin/redis/rate-limit:** `{resp.json()}`")
        md.append("")

    # -- 6. Quota --
    md.append("## 5. Redis Quota Proof")
    md.append("")
    quota_keys = r.keys("quota:*")
    md.append(f"| Check | Result |")
    md.append(f"|-------|--------|")
    md.append(f"| Quota Keys | `{quota_keys}` |")
    for k in quota_keys:
        md.append(f"| {k} | value=`{r.get(k)}`, TTL=`{r.ttl(k)}s` |")
    md.append("")
    evidence["Redis Quotas"] = len(quota_keys) >= 1

    # -- 7. JWT Blacklist --
    md.append("## 6. JWT Blacklist Proof")
    md.append("")
    resp = client.post("/api/v1/auth/logout", headers=headers)
    blacklist_keys = r.keys("blacklist:*")

    resp_after = client.post("/api/v1/chat", json={"prompt": "fail"}, headers=headers)
    resp_check = client.get("/api/v1/auth/check", headers=headers)

    md.append(f"| Check | Result |")
    md.append(f"|-------|--------|")
    md.append(f"| POST /auth/logout | `{resp.status_code}` |")
    md.append(f"| Blacklist Keys | `{len(blacklist_keys)}` |")
    md.append(f"| POST /chat (revoked) | `{resp_after.status_code}` |")
    md.append(f"| GET /auth/check (revoked) | `{resp_check.status_code}` |")
    md.append("")
    evidence["JWT Blacklist"] = resp_after.status_code == 401 and resp_check.status_code == 401

    # -- 8. Full Key Dump --
    md.append("## 7. Full Redis Key Dump")
    md.append("")
    md.append("```")
    md.append("redis-cli KEYS \"*\"")
    all_keys = sorted(r.keys("*"))
    for k in all_keys:
        ttl = r.ttl(k)
        md.append(f"  {k}  (TTL: {ttl}s)")
    md.append(f"\nTotal keys: {len(all_keys)}")
    md.append("```")
    md.append("")

    # -- 9. Admin Key Inspection --
    md.append("## 8. Admin Key Inspection API")
    md.append("")
    # Need fresh admin token (previous wasn't logged out)
    admin_resp2 = client.post("/api/v1/auth/login", data={"username": "admin@route.com", "password": "adminpassword"})
    if admin_resp2.status_code == 200:
        admin_token2 = admin_resp2.json()["access_token"]
        admin_headers2 = {"Authorization": f"Bearer {admin_token2}"}
        resp = client.get("/api/v1/admin/redis/keys", headers=admin_headers2)
        if resp.status_code == 200:
            md.append("```json")
            md.append(json.dumps(resp.json(), indent=2))
            md.append("```")
            md.append("")

    # -- Final Summary --
    md.append("## Final Validation Summary")
    md.append("")
    md.append("| Feature | Status |")
    md.append("|---------|--------|")
    for feature, passed in evidence.items():
        icon = "✅ PROVEN" if passed else "❌ FAILED"
        md.append(f"| {feature} | {icon} |")

    all_passed = all(evidence.values())
    md.append("")
    md.append(f"**OVERALL: {'✅ ALL REDIS FEATURES VALIDATED' if all_passed else '❌ SOME FEATURES FAILED'}**")
    md.append("")
    md.append("---")
    md.append(f"*Report generated at {datetime.datetime.now().isoformat()}*")

    report_content = "\n".join(md)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"[OK] Validation report saved to {REPORT_FILE}")
    print()
    for feature, passed in evidence.items():
        icon = "[OK]" if passed else "[FAIL]"
        print(f"  {icon} {feature}")
    print()
    print(f"  OVERALL: {'ALL PROVEN' if all_passed else 'FAILURES DETECTED'}")

    # Cleanup
    db = SessionLocal()
    try:
        cleanup(db)
    finally:
        db.close()

if __name__ == "__main__":
    main()
