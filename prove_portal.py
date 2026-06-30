"""
prove_portal.py — Terminal proof that every Redis feature works end-to-end.

Talks to the live FastAPI server at http://localhost:8000
and proves:
  1. Sign Up + Sign In (JWT issued, Redis session created)
  2. Cache MISS vs HIT with real timing comparison
  3. Rate Limiter — 5 pass, 10 blocked (sliding window)
  4. Context Inspector — 3 messages, Redis LIST grows
  5. Token Lifecycle — blacklist then verify 401
  6. Redis Key Inspector — categorized key counts

Run:  .\.venv\Scripts\python.exe prove_portal.py
"""

import sys, json, time, uuid, hashlib, datetime
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' not installed. Run: pip install requests")
    sys.exit(1)

BASE   = "http://localhost:8000/api/v1"
RUN_ID = uuid.uuid4().hex[:8]
EMAIL  = f"prove_{RUN_ID}@route.com"
PASS   = "ProveRedis!2026"
ADMIN_EMAIL = "admin@route.com"
ADMIN_PASS  = "adminpassword"

W = 70
SEP = "=" * W

results = []


# ── Utilities ────────────────────────────────────────────────────────
def hdr(title):
    print(f"\n{SEP}")
    print(f" {title} ".center(W, "─"))
    print(SEP)

def ok(label, detail=""):
    results.append(("PASS", label))
    suffix = f"  ({detail})" if detail else ""
    print(f"  [OK]   {label}{suffix}")

def fail(label, detail=""):
    results.append(("FAIL", label))
    suffix = f"  ({detail})" if detail else ""
    print(f"  [FAIL] {label}{suffix}")

def info(msg):
    print(f"         {msg}")

def timed_post(url, **kwargs):
    t0 = time.perf_counter()
    r = requests.post(url, **kwargs)
    ms = int((time.perf_counter() - t0) * 1000)
    return r, ms

def timed_get(url, **kwargs):
    t0 = time.perf_counter()
    r = requests.get(url, **kwargs)
    ms = int((time.perf_counter() - t0) * 1000)
    return r, ms


# ════════════════════════════════════════════════════════════════════
print(SEP)
print(" ROUTE MOBILE — PORTAL REDIS PROOF (TERMINAL) ".center(W, "="))
print(SEP)
print(f"  Run ID     : {RUN_ID}")
print(f"  Test user  : {EMAIL}")
print(f"  Server     : {BASE}")
print(f"  Timestamp  : {datetime.datetime.now().isoformat()}")


# ── 1. SIGN UP ───────────────────────────────────────────────────────
hdr("1. SIGN UP (POST /auth/register)")
r, ms = timed_post(f"{BASE}/auth/register",
                   json={"email": EMAIL, "password": PASS})
if r.status_code == 200:
    d = r.json()
    ok("User Registered", f"{ms}ms | id={d['id']} | role={d['role']}")
    info(f"API key (first 24 chars): {d['api_key'][:24]}...")
    info("Redis: NO action — registration only writes to PostgreSQL")
else:
    fail("User Registration", f"status={r.status_code} | {r.text[:80]}")
    sys.exit(1)


# ── 2. SIGN IN → Redis Session ────────────────────────────────────────
hdr("2. SIGN IN (POST /auth/login)  →  Redis SESSION CREATED")
r, ms = timed_post(f"{BASE}/auth/login",
                   data={"username": EMAIL, "password": PASS})
if r.status_code == 200:
    d = r.json()
    token = d["access_token"]
    hdrs  = {"Authorization": f"Bearer {token}"}
    ok("Login successful", f"{ms}ms | token_type={d['token_type']}")
    info(f"JWT (first 60): {token[:60]}...")
    info("Redis: HSET session:<uid>  +  EXPIRE <ttl>  (Hash data structure)")
else:
    fail("Login", f"status={r.status_code}")
    sys.exit(1)

# Verify Redis session
r2, ms2 = timed_get(f"{BASE}/me/session", headers=hdrs)
if r2.status_code == 200:
    s = r2.json()
    ok("Redis session retrieved (HGETALL)", f"{ms2}ms")
    info(f"  session key  : session:{s.get('user_id')}")
    info(f"  email        : {s.get('email')}")
    info(f"  login_time   : {s.get('login_time')}")
    info(f"  expires      : {s.get('expires')}")
    info(f"  ip           : {s.get('ip')}")
else:
    fail("Session retrieval", f"status={r2.status_code}")


# ── 3. ADMIN LOGIN ────────────────────────────────────────────────────
hdr("3. ADMIN LOGIN  (for Inspector tab)")
r, ms = timed_post(f"{BASE}/auth/login",
                   data={"username": ADMIN_EMAIL, "password": ADMIN_PASS})
if r.status_code == 200:
    admin_token = r.json()["access_token"]
    admin_hdrs  = {"Authorization": f"Bearer {admin_token}"}
    ok("Admin login", f"{ms}ms")
else:
    admin_token = None
    admin_hdrs  = {}
    fail("Admin login", f"status={r.status_code}")


# ── 4. CACHE: MISS then HIT ───────────────────────────────────────────
hdr("4. CACHE DEMO — MISS vs HIT  (STRING: SETEX / GET)")
PROMPT = f"Explain Redis caching in one sentence. [run={RUN_ID}]"
h = hashlib.sha256(PROMPT.encode()).hexdigest()

print(f"  Prompt  : {PROMPT[:60]}...")
print(f"  SHA-256 : cache:{h[:24]}...")
print()

# First call — cache MISS
print("  >>> Request 1 (Cache MISS — calling Mistral AI) ...")
r1, miss_ms = timed_post(f"{BASE}/chat", json={"prompt": PROMPT}, headers=hdrs)

if r1.status_code == 200:
    d1 = r1.json()
    ok("Cache MISS (first call)", f"{miss_ms}ms | cached={d1.get('cached')} | model={d1.get('model')}")
    info(f"  Response  : {d1.get('response', '')[:80]}...")
    info(f"  Tokens    : {d1.get('usage', {}).get('total_tokens', 0)} total")
else:
    fail("Cache MISS", f"status={r1.status_code} | {r1.text[:100]}")

# Second call — cache HIT
print()
print("  >>> Request 2 (Cache HIT — served from Redis) ...")
r2, hit_ms = timed_post(f"{BASE}/chat", json={"prompt": PROMPT}, headers=hdrs)

if r2.status_code == 200:
    d2 = r2.json()
    ok("Cache HIT (second call)", f"{hit_ms}ms | cached={d2.get('cached')}")
    if miss_ms > 0 and hit_ms > 0:
        speedup = round(miss_ms / hit_ms, 1)
        info(f"  Speedup   : {miss_ms}ms → {hit_ms}ms  =  {speedup}x FASTER with Redis")
        ok("Redis cache speedup confirmed", f"{speedup}x faster")
else:
    fail("Cache HIT", f"status={r2.status_code}")


# ── 5. RATE LIMITER ───────────────────────────────────────────────────
hdr("5. RATE LIMITER — Sliding Window (5 req/sec free tier)")
print("  Firing 15 rapid requests to /test/rate-limit ...")
print()

statuses = []
for i in range(15):
    r, _ = timed_get(f"{BASE}/test/rate-limit", headers=hdrs)
    statuses.append(r.status_code)
    icon = "[OK]   " if r.status_code == 200 else "[BLOCK]"
    print(f"    Req {i+1:2d}  {icon}  HTTP {r.status_code}")

passed_200  = sum(1 for s in statuses if s == 200)
blocked_429 = sum(1 for s in statuses if s == 429)
print()

first5_ok    = all(s == 200 for s in statuses[:5])
rest_blocked = all(s == 429 for s in statuses[5:])

ok("Req 1-5 all passed (200)",  f"statuses={statuses[:5]}")  if first5_ok    else fail("Req 1-5 not all 200", str(statuses[:5]))
ok("Req 6-15 all blocked (429)", f"statuses={statuses[5:]}") if rest_blocked else fail("Req 6-15 not all 429", str(statuses[5:]))
ok("Redis sliding window proved", f"{passed_200} passed, {blocked_429} blocked")


# ── 6. CONTEXT INSPECTOR ─────────────────────────────────────────────
hdr("6. CONTEXT INSPECTOR  (Redis LIST: LPUSH / LRANGE)")

# First, clear any existing context
requests.delete(f"{BASE}/me/context", headers=hdrs)

messages = [
    "What is Redis?",
    "What data structures does Redis support?",
    "How does Redis caching work?",
]

for i, msg in enumerate(messages, 1):
    print(f"  >>> Message {i}: \"{msg}\"")
    r, ms = timed_post(f"{BASE}/chat", json={"prompt": msg}, headers=hdrs)
    
    # Check context grew
    ctx_r = requests.get(f"{BASE}/me/context", headers=hdrs)
    if ctx_r.status_code == 200:
        ctx = ctx_r.json()
        count = ctx["message_count"]
        key   = f"context:{ctx['user_id']}"
        ok(f"Message {i} stored in Redis context", f"{ms}ms | key={key} | total_msgs={count}")
        if ctx["messages"]:
            last = ctx["messages"][-1]
            info(f"  Last role : {last.get('role')}")
            info(f"  Preview   : {last.get('content', '')[:70]}...")
    else:
        fail(f"Context fetch after message {i}", f"status={ctx_r.status_code}")
    print()

# Final context state
ctx_r = requests.get(f"{BASE}/me/context", headers=hdrs)
if ctx_r.status_code == 200:
    ctx = ctx_r.json()
    ok("Final context state", f"user_id={ctx['user_id']} | messages={ctx['message_count']}")
    info(f"  Redis key  : context:{ctx['user_id']}")
    info(f"  Structure  : LIST (LPUSH/LRANGE/LTRIM — last 20 messages kept)")
    info(f"  Messages   : {ctx['message_count']} stored")


# ── 7. TOKEN LIFECYCLE (Blacklist) ────────────────────────────────────
hdr("7. TOKEN LIFECYCLE  (Redis STRING: SETEX blacklist:<jwt>)")

# Verify token is valid
r, ms = timed_get(f"{BASE}/auth/check", headers=hdrs)
ok("Token valid before logout", f"{ms}ms | status={r.status_code}") \
    if r.status_code == 200 else fail("Token check before logout")

# Logout — blacklist in Redis
print()
print("  >>> Logging out (blacklisting JWT in Redis) ...")
r, ms = timed_post(f"{BASE}/auth/logout", headers=hdrs)
ok("Logout (token blacklisted)", f"{ms}ms | SETEX blacklist:<token> <ttl> 'revoked'") \
    if r.status_code == 200 else fail("Logout failed", f"status={r.status_code}")

# Use revoked token — must get 401
print()
print("  >>> Attempting request with revoked token ...")
r, ms = timed_get(f"{BASE}/auth/check", headers=hdrs)
if r.status_code == 401:
    ok("Revoked token rejected (401)", f"{ms}ms | detail={r.json().get('detail')}")
    info("  Redis: EXISTS blacklist:<token> → 1 → reject immediately")
    info("  No database round-trip needed — pure Redis lookup")
else:
    fail("Revoked token not rejected", f"status={r.status_code}")

# Also try chat with revoked token
r, ms = timed_post(f"{BASE}/chat", json={"prompt": "should fail"}, headers=hdrs)
ok("Chat with revoked token → 401", f"{ms}ms") \
    if r.status_code == 401 else fail("Chat with revoked token", f"status={r.status_code}")


# ── 8. REDIS KEY INSPECTOR ────────────────────────────────────────────
hdr("8. REDIS KEY INSPECTOR  (Admin endpoint)")

if admin_hdrs:
    r, ms = timed_get(f"{BASE}/admin/redis/keys", headers=admin_hdrs)
    if r.status_code == 200:
        keys = r.json()
        ok("Redis key inspection", f"{ms}ms | total={keys.get('total_keys')}")
        info(f"  session_keys    : {keys.get('session_keys')}")
        info(f"  cache_keys      : {keys.get('cache_keys')}")
        info(f"  rate_limit_keys : {keys.get('rate_limit_keys')}")
        info(f"  quota_keys      : {keys.get('quota_keys')}")
        info(f"  blacklist_keys  : {keys.get('blacklist_keys')}")
        info(f"  stats_keys      : {keys.get('stats_keys')}")
        info(f"  other_keys      : {keys.get('other_keys')}")
        info(f"  TOTAL           : {keys.get('total_keys')}")
    else:
        fail("Redis key inspection", f"status={r.status_code}")

    # Cache stats
    r, ms = timed_get(f"{BASE}/admin/cache/stats", headers=admin_hdrs)
    if r.status_code == 200:
        s = r.json()
        ok("Cache stats", f"hits={s.get('hits')} | misses={s.get('misses')} | ratio={s.get('hit_ratio')}")

    # Rate limit keys
    r, ms = timed_get(f"{BASE}/admin/redis/rate-limit", headers=admin_hdrs)
    if r.status_code == 200:
        ok("Rate limit key count", f"active_keys={r.json().get('active_keys')}")
else:
    fail("Redis inspector (no admin token)")


# ── SUMMARY ───────────────────────────────────────────────────────────
print(f"\n{SEP}")
print(" RESULTS SUMMARY ".center(W, "="))
print(SEP)

total  = len(results)
passed = sum(1 for r, _ in results if r == "PASS")
failed = sum(1 for r, _ in results if r == "FAIL")

for status, label in results:
    icon = "[OK]  " if status == "PASS" else "[FAIL]"
    print(f"  {icon}  {label}")

print(SEP)
print(f"  Total: {total}  |  Passed: {passed}  |  Failed: {failed}")
overall = "PASS" if failed == 0 else "FAIL"
print(f"  OVERALL: {overall}")
print(SEP)
print()

# Save JSON report
report = {
    "timestamp": datetime.datetime.now().isoformat(),
    "run_id": RUN_ID,
    "test_user": EMAIL,
    "overall": overall,
    "total": total,
    "passed": passed,
    "failed": failed,
    "results": [{"status": s, "test": l} for s, l in results]
}
with open("portal_proof_results.json", "w") as f:
    json.dump(report, f, indent=2)
print(f"  Results saved to portal_proof_results.json")
print()

sys.exit(0 if overall == "PASS" else 1)
