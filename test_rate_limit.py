"""
test_rate_limit.py — Explicit rate-limit assertions.

Proves:
  Free plan = 5 req/sec
  Requests 1-5   → 200
  Requests 6-15  → 429

Requires: Redis running on localhost:6379
"""
import sys, os, json, time
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

# -- helpers --
DEMO_EMAIL = "ratelimit_test_user@route.com"
DEMO_PASS  = "RateLimitTest!2026"

def cleanup(db):
    user = db.query(models.User).filter(models.User.email == DEMO_EMAIL).first()
    if user:
        db.query(models.AuditLog).filter(models.AuditLog.user_id == user.id).delete()
        db.query(models.UsageRecord).filter(models.UsageRecord.user_id == user.id).delete()
        db.query(models.APIKey).filter(models.APIKey.user_id == user.id).delete()
        db.delete(user)
        db.commit()

def flush_rate_keys():
    """Clear any lingering rate_limit keys so the test starts clean."""
    import redis
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    for key in r.keys("rate_limit:*"):
        r.delete(key)

# -- main --
def main():
    # Flush stale rate keys
    flush_rate_keys()

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

    # 3. Send 15 rapid requests — free plan limit is 5/sec
    results = []
    print("\n--- Rate Limit Test (Free plan = 5 req/sec) ---")
    for i in range(1, 16):
        r = client.get("/api/v1/test/rate-limit", headers=headers)
        results.append(r.status_code)
        status_icon = "[OK]" if r.status_code == 200 else "[BLOCKED]"
        print(f"  Request {i:2d} → {r.status_code}  {status_icon}")

    blocked_429 = sum(1 for s in results if s == 429)
    if blocked_429 > 0:
        print("Redis Rate Limiter Triggered")
    else:
        print("ERROR: Redis Rate Limiter not triggered!")
        sys.exit(1)

    # 4. EXPLICIT ASSERTIONS
    print("\n--- Assertions ---")

    # First 5 must be 200
    for i in range(5):
        assert results[i] == 200, (
            f"FAIL: Request {i+1} expected 200, got {results[i]}"
        )
    print("[OK] Requests 1-5   → 200  (all passed)")

    # Requests 6-15 must be 429
    for i in range(5, 15):
        assert results[i] == 429, (
            f"FAIL: Request {i+1} expected 429, got {results[i]}"
        )
    print("[OK] Requests 6-15  → 429  (all rate-limited)")

    rate_limited = sum(1 for s in results if s == 429)
    passed       = sum(1 for s in results if s == 200)

    summary = {
        "test": "rate_limit",
        "plan": "free",
        "limit": "5 req/sec",
        "total_requests": 15,
        "passed_200": passed,
        "blocked_429": rate_limited,
        "result": "PASS" if rate_limited >= 10 else "FAIL",
        "details": [{"request": i+1, "status": s} for i, s in enumerate(results)]
    }
    with open("test_rate_limit_results.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n{'='*50}")
    print(f"RATE LIMIT TEST: {summary['result']}")
    print(f"  Allowed: {passed}/5  |  Blocked: {rate_limited}/10")
    print(f"  Results saved to test_rate_limit_results.json")
    print(f"{'='*50}")

    # Cleanup
    db = SessionLocal()
    try:
        cleanup(db)
    finally:
        db.close()

if __name__ == "__main__":
    main()
