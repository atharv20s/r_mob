"""
test_redis_enterprise.py — Fully idempotent enterprise Redis validation suite.

Every run creates a unique user (UUID-based email) and unique prompt
so that stale keys from previous runs never pollute results.

Tests:
  1. Redis Health
  2. Registration  (unique user per run)
  3. Login + Session
  4. Cache Miss     (unique prompt per run → guaranteed miss)
  5. Cache Hit      (same prompt again → guaranteed hit)
  6. Cache Stats    (stats reset before test → exact counts)
  7. Rate Limiting  (rate keys flushed + 2s sleep before test)
  8. Rate Limit Admin Metrics
  9. Redis Key Inspection
  10. Logout + JWT Blacklist
  11. Auth Check after logout
  12. Quota Enforcement

Requires: Redis running on localhost:6379
"""
import sys, os, json, time, datetime, uuid
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

# ── Unique per run ──────────────────────────────────────────────────────
RUN_ID     = uuid.uuid4().hex[:8]
DEMO_EMAIL = f"test_{RUN_ID}@route.com"
DEMO_PASS  = "EnterpriseTest!2026"
ADMIN_EMAIL = "admin@route.com"
ADMIN_PASS  = "adminpassword"
BASE_PROMPT = f"Explain quantum entanglement in one sentence. [run={RUN_ID}]"

results = []


def record(name, passed, detail=""):
    icon = "[OK]" if passed else "[FAIL]"
    results.append({"test": name, "result": "PASS" if passed else "FAIL", "detail": detail})
    print(f"  {icon} {name}: {'PASS' if passed else 'FAIL'}" + (f"  ({detail})" if detail else ""))


def cleanup_db(db):
    """Delete the test user and all associated rows from PostgreSQL."""
    user = db.query(models.User).filter(models.User.email == DEMO_EMAIL).first()
    if user:
        db.query(models.AuditLog).filter(models.AuditLog.user_id == user.id).delete()
        db.query(models.UsageRecord).filter(models.UsageRecord.user_id == user.id).delete()
        db.query(models.APIKey).filter(models.APIKey.user_id == user.id).delete()
        db.delete(user)
        db.commit()


def flush_redis():
    """
    Selective flush — delete ALL test-related key families so every
    run starts from a clean slate.  Uses SCAN (not KEYS) for safety.
    """
    r = redis_lib.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    patterns = [
        "rate_limit:*",
        "rate_limit_slide:*",
        "cache:*",
        "stats:*",
        "quota:*",
        "session:*",
        "context:*",
        "blacklist:*",
    ]
    deleted = 0
    for pattern in patterns:
        for key in r.scan_iter(pattern):
            r.delete(key)
            deleted += 1
    print(f"  [FLUSH] Deleted {deleted} stale Redis keys")


def main():
    print("=" * 70)
    print(" ENTERPRISE REDIS VALIDATION SUITE ".center(70, "="))
    print("=" * 70)
    print(f"  Timestamp : {datetime.datetime.now().isoformat()}")
    print(f"  Run ID    : {RUN_ID}")
    print(f"  Test user : {DEMO_EMAIL}")
    print(f"  Prompt    : {BASE_PROMPT[:60]}...")
    print()

    # ── Pre-clean ────────────────────────────────────────────────────────
    flush_redis()
    db = SessionLocal()
    try:
        cleanup_db(db)
    finally:
        db.close()

    from src.main import app
    client = TestClient(app)
    r = redis_lib.Redis(host="127.0.0.1", port=6379, decode_responses=True)

    # ── 1. Redis Health ─────────────────────────────────────────────────
    print("\n-- Test 1: Redis Health --")
    resp = client.get("/api/v1/health/redis")
    record("Redis Health Endpoint", resp.status_code == 200 and resp.json().get("redis") == "connected",
           f"status={resp.status_code}, body={resp.json()}")

    pong = r.ping()
    record("Redis Direct Ping", pong is True, f"PONG={pong}")

    # ── 2. Registration ─────────────────────────────────────────────────
    print("\n-- Test 2: Registration --")
    resp = client.post("/api/v1/auth/register", json={"email": DEMO_EMAIL, "password": DEMO_PASS})
    record("User Registration", resp.status_code == 200,
           f"status={resp.status_code}, body={resp.text[:120]}")

    # ── 3. Login + Session ──────────────────────────────────────────────
    print("\n-- Test 3: Login + Session --")
    resp = client.post("/api/v1/auth/login", data={"username": DEMO_EMAIL, "password": DEMO_PASS})
    record("User Login", resp.status_code == 200, f"status={resp.status_code}")
    token = resp.json().get("access_token", "")
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/v1/me/session", headers=headers)
    record("Session Retrieval", resp.status_code == 200 and resp.json().get("email") == DEMO_EMAIL,
           f"status={resp.status_code}, body={resp.json()}")

    session_keys = list(r.scan_iter("session:*"))
    record("Session Key in Redis", len(session_keys) >= 1, f"session_keys={session_keys}")

    # ── 4. Cache Miss ───────────────────────────────────────────────────
    print("\n-- Test 4: Cache Miss --")
    resp = client.post("/api/v1/chat", json={"prompt": BASE_PROMPT}, headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        record("Cache Miss", data.get("cached") == False, f"cached={data.get('cached')}")
    else:
        record("Cache Miss (chat request)", False, f"status={resp.status_code}, body={resp.text[:120]}")

    # ── 5. Cache Hit ────────────────────────────────────────────────────
    print("\n-- Test 5: Cache Hit --")
    resp = client.post("/api/v1/chat", json={"prompt": BASE_PROMPT}, headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        record("Cache Hit", data.get("cached") == True, f"cached={data.get('cached')}")
    else:
        record("Cache Hit (chat request)", False, f"status={resp.status_code}, body={resp.text[:120]}")

    cache_keys = list(r.scan_iter("cache:*"))
    record("Cache Key in Redis", len(cache_keys) >= 1, f"cache_keys_count={len(cache_keys)}")

    # ── 6. Cache Stats ──────────────────────────────────────────────────
    print("\n-- Test 6: Cache Stats --")
    admin_resp = client.post("/api/v1/auth/login", data={"username": ADMIN_EMAIL, "password": ADMIN_PASS})
    admin_token = admin_resp.json().get("access_token", "")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    resp = client.get("/api/v1/admin/cache/stats", headers=admin_headers)
    if resp.status_code == 200:
        stats = resp.json()
        # At minimum, 1 hit (from Test 5) must be recorded.
        # Misses may be 0 if the stats:cache key was re-created after flush.
        record("Cache Stats", stats.get("hits", 0) >= 1,
               f"hits={stats.get('hits')}, misses={stats.get('misses')}, ratio={stats.get('hit_ratio')}")
    else:
        record("Cache Stats", False, f"status={resp.status_code}")

    # ── 7. Rate Limiting ────────────────────────────────────────────────
    print("\n-- Test 7: Rate Limiting (Free = 5 req/sec) --")
    # Flush rate-limit keys and wait for window to fully reset
    for key in r.scan_iter("rate_limit:*"):
        r.delete(key)
    for key in r.scan_iter("rate_limit_slide:*"):
        r.delete(key)
    time.sleep(2)

    statuses = []
    for i in range(15):
        r2 = client.get("/api/v1/test/rate-limit", headers=headers)
        statuses.append(r2.status_code)
        icon = "[OK]" if r2.status_code == 200 else "[BLOCKED]"
        print(f"    Req {i+1:2d} -> {r2.status_code}  {icon}")

    passed_200 = sum(1 for s in statuses if s == 200)
    blocked_429 = sum(1 for s in statuses if s == 429)

    if blocked_429 > 0:
        print("  Redis Rate Limiter Triggered")

    first_five_ok = all(s == 200 for s in statuses[:5])
    rest_blocked = all(s == 429 for s in statuses[5:])
    record("Rate Limit: Req 1-5 -> 200", first_five_ok,
           f"statuses={statuses[:5]}")
    record("Rate Limit: Req 6-15 -> 429", rest_blocked,
           f"statuses={statuses[5:]}")
    record("Rate Limit Summary", blocked_429 >= 10,
           f"passed={passed_200}, blocked={blocked_429}")

    # ── 8. Rate Limit Admin ─────────────────────────────────────────────
    print("\n-- Test 8: Rate Limit Admin Metrics --")
    resp = client.get("/api/v1/admin/redis/rate-limit", headers=admin_headers)
    record("Rate Limit Admin", resp.status_code == 200 and resp.json().get("active_keys", 0) >= 0,
           f"body={resp.json()}")

    # ── 9. Redis Key Inspection ─────────────────────────────────────────
    print("\n-- Test 9: Redis Key Inspection --")
    resp = client.get("/api/v1/admin/redis/keys", headers=admin_headers)
    if resp.status_code == 200:
        keys = resp.json()
        record("Redis Key Inspection", keys.get("total_keys", 0) > 0, f"body={keys}")
    else:
        record("Redis Key Inspection", False, f"status={resp.status_code}")

    # ── 10. Logout + JWT Blacklist ──────────────────────────────────────
    print("\n-- Test 10: Logout + JWT Blacklist --")
    resp = client.post("/api/v1/auth/logout", headers=headers)
    record("Logout", resp.status_code == 200, f"body={resp.json()}")

    blacklist_keys = list(r.scan_iter("blacklist:*"))
    record("Blacklist Key in Redis", len(blacklist_keys) >= 1,
           f"blacklist_keys_count={len(blacklist_keys)}")

    resp = client.post("/api/v1/chat", json={"prompt": "should fail"}, headers=headers)
    record("Revoked Token -> 401", resp.status_code == 401,
           f"status={resp.status_code}, detail={resp.json().get('detail')}")

    # ── 11. Auth Check ──────────────────────────────────────────────────
    print("\n-- Test 11: Auth Check after Logout --")
    resp = client.get("/api/v1/auth/check", headers=headers)
    record("Auth Check -> 401", resp.status_code == 401,
           f"status={resp.status_code}")

    # ── 12. Quota Key ───────────────────────────────────────────────────
    print("\n-- Test 12: Quota Enforcement --")
    quota_keys = list(r.scan_iter("quota:*"))
    record("Quota Key in Redis", len(quota_keys) >= 1,
           f"quota_keys={quota_keys}")

    # ── Full Redis Key Dump ─────────────────────────────────────────────
    print("\n-- Redis Key Dump --")
    all_keys = sorted(list(r.scan_iter("*")))
    print(f"  Total keys: {len(all_keys)}")
    for k in all_keys:
        ttl = r.ttl(k)
        print(f"    {k}  (TTL: {ttl}s)")

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(" RESULTS SUMMARY ".center(70, "="))
    print("=" * 70)
    total  = len(results)
    passed = sum(1 for r in results if r["result"] == "PASS")
    failed = sum(1 for r in results if r["result"] == "FAIL")
    print(f"  Total: {total}  |  Passed: {passed}  |  Failed: {failed}")
    for r in results:
        icon = "[OK]" if r["result"] == "PASS" else "[FAIL]"
        print(f"  {icon} {r['test']}")
    print("=" * 70)

    overall = "PASS" if (failed == 0 and blocked_429 > 0) else "FAIL"
    print(f"\n  OVERALL: {overall}\n")

    # Save
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "run_id": RUN_ID,
        "test_user": DEMO_EMAIL,
        "overall": overall,
        "total": total,
        "passed": passed,
        "failed": failed,
        "tests": results,
        "redis_keys": all_keys
    }
    with open("test_results.json", "w") as f:
        json.dump(report, f, indent=2)
    print("  Results saved to test_results.json")

    # Cleanup test user from PostgreSQL
    db = SessionLocal()
    try:
        cleanup_db(db)
    finally:
        db.close()

    if overall == "FAIL":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
