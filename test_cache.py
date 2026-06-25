"""
test_cache.py — Explicit cache hit/miss assertions.

Proves:
  Request #1  →  cached=false  (cache miss → calls Mistral)
  Request #2  →  cached=true   (cache hit  → served from Redis)

Requires: Redis running on localhost:6379
"""
import sys, os, json
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from fastapi.testclient import TestClient
from src.db.session import SessionLocal
from src.db import models

DEMO_EMAIL = "cache_test_user@route.com"
DEMO_PASS  = "CacheTest!2026"
PROMPT     = "What is the speed of light in vacuum?"

def cleanup(db):
    user = db.query(models.User).filter(models.User.email == DEMO_EMAIL).first()
    if user:
        db.query(models.AuditLog).filter(models.AuditLog.user_id == user.id).delete()
        db.query(models.UsageRecord).filter(models.UsageRecord.user_id == user.id).delete()
        db.query(models.APIKey).filter(models.APIKey.user_id == user.id).delete()
        db.delete(user)
        db.commit()

def flush_cache_keys():
    """Clear any lingering cache keys so the test starts clean."""
    import redis
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    for key in r.keys("cache:*"):
        r.delete(key)
    # Also reset stats
    r.delete("stats:cache_hits")
    r.delete("stats:cache_misses")

def main():
    flush_cache_keys()

    from src.main import app
    client = TestClient(app)
    db = SessionLocal()

    try:
        cleanup(db)
    finally:
        db.close()

    # 1. Register
    resp = client.post("/api/v1/auth/register", json={
        "email": DEMO_EMAIL, "password": DEMO_PASS
    })
    assert resp.status_code == 200, f"Register failed: {resp.text}"
    print("[OK] Registration OK")

    # 2. Login
    resp = client.post("/api/v1/auth/login", data={
        "username": DEMO_EMAIL, "password": DEMO_PASS
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("[OK] Login OK")

    # 3. First request — MUST be cache miss
    print("\n--- Cache Miss Test ---")
    resp1 = client.post("/api/v1/chat", json={"prompt": PROMPT}, headers=headers)
    assert resp1.status_code == 200, f"Chat request 1 failed: {resp1.text}"
    data1 = resp1.json()
    print(f"  Request #1: cached={data1['cached']}")
    assert data1["cached"] == False, (
        f"FAIL: First request expected cached=false, got cached={data1['cached']}"
    )
    print("  [OK] ASSERT PASSED: cached == false")

    # 4. Second request (same prompt) — MUST be cache hit
    print("\n--- Cache Hit Test ---")
    resp2 = client.post("/api/v1/chat", json={"prompt": PROMPT}, headers=headers)
    assert resp2.status_code == 200, f"Chat request 2 failed: {resp2.text}"
    data2 = resp2.json()
    print(f"  Request #2: cached={data2['cached']}")
    assert data2["cached"] == True, (
        f"FAIL: Second request expected cached=true, got cached={data2['cached']}"
    )
    print("  [OK] ASSERT PASSED: cached == true")

    # 5. Verify responses are identical
    assert data1["response"] == data2["response"], (
        "FAIL: Cached response differs from original response"
    )
    print("  [OK] ASSERT PASSED: cached response matches original")

    # 6. Login as admin and check cache stats
    print("\n--- Cache Stats Test ---")
    admin_resp = client.post("/api/v1/auth/login", data={
        "username": "admin@route.com", "password": "adminpassword"
    })
    if admin_resp.status_code == 200:
        admin_token = admin_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        stats_resp = client.get("/api/v1/admin/cache/stats", headers=admin_headers)
        if stats_resp.status_code == 200:
            stats = stats_resp.json()
            print(f"  Cache Stats: hits={stats['hits']}, misses={stats['misses']}, ratio={stats['hit_ratio']}")
            assert stats["hits"] >= 1, f"FAIL: Expected at least 1 cache hit, got {stats['hits']}"
            assert stats["misses"] >= 1, f"FAIL: Expected at least 1 cache miss, got {stats['misses']}"
            print("  [OK] ASSERT PASSED: cache stats show hits >= 1 AND misses >= 1")

    # 7. Check Redis keys
    print("\n--- Redis Key Verification ---")
    import redis
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    cache_keys = r.keys("cache:*")
    print(f"  Cache keys in Redis: {len(cache_keys)}")
    for k in cache_keys:
        print(f"    → {k}")
    assert len(cache_keys) >= 1, "FAIL: No cache keys found in Redis"
    print("  [OK] ASSERT PASSED: cache keys exist in Redis")

    summary = {
        "test": "cache_hit_miss",
        "prompt": PROMPT,
        "request_1_cached": data1["cached"],
        "request_2_cached": data2["cached"],
        "responses_match": data1["response"] == data2["response"],
        "cache_keys_in_redis": len(cache_keys),
        "result": "PASS"
    }
    with open("test_cache_results.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*50}")
    print(f"CACHE TEST: PASS")
    print(f"  Miss → Hit verified with explicit assertions")
    print(f"  Results saved to test_cache_results.json")
    print(f"{'='*50}")

    # Cleanup
    db = SessionLocal()
    try:
        cleanup(db)
    finally:
        db.close()

if __name__ == "__main__":
    main()
