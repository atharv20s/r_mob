#!/usr/bin/env python3
"""
Redis Connection Tester
========================
Standalone script to verify Redis connectivity BEFORE starting the gateway.

Usage (from project root):
    python scripts/test_redis_connection.py
"""

import sys
import os
import io
import time

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to path so we can import settings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import redis
except ImportError:
    print("[X] redis-py not installed. Run: pip install redis")
    sys.exit(1)


# Configuration — read from project settings, fall back to defaults
try:
    from src.core.config import settings
    REDIS_URL = settings.REDIS_URL
except Exception:
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6337")

DIVIDER = "-" * 60


def print_header(title):
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def test_ping(client):
    """Test 1: Basic PING/PONG"""
    print_header("1. PING Test")
    try:
        result = client.ping()
        print(f"  PING  -> {'PONG [OK]' if result else 'FAILED [X]'}")
        return result
    except redis.ConnectionError as e:
        print(f"  [X] Connection failed: {e}")
        return False


def test_read_write(client):
    """Test 2: SET/GET round-trip"""
    print_header("2. Read/Write Test")
    test_key = "__connection_test__"
    test_val = f"test_{int(time.time())}"
    try:
        client.set(test_key, test_val, ex=10)
        got = client.get(test_key)
        ok = got == test_val
        print(f"  SET   -> {test_key} = {test_val}")
        print(f"  GET   -> {got}")
        print(f"  Match -> {'[OK]' if ok else '[X]'}")
        client.delete(test_key)
        return ok
    except Exception as e:
        print(f"  [X] Read/Write failed: {e}")
        return False


def test_server_info(client):
    """Test 3: Server metadata + client/command stats"""
    print_header("3. Server Info")
    try:
        info = client.info("server")
        internal_port = info.get('tcp_port', '?')
        print(f"  Redis version  : {info.get('redis_version', '?')}")
        print(f"  OS             : {info.get('os', '?')}")
        print(f"  Uptime (sec)   : {info.get('uptime_in_seconds', '?')}")
        print(f"  Internal port  : {internal_port}  (container listens here)")
        print(f"  Host port      : {REDIS_URL.rsplit(':', 1)[-1]}  (Docker publishes here)")
        if str(internal_port) != REDIS_URL.rsplit(':', 1)[-1]:
            print(f"                   ^ Different ports is normal -- Docker port mapping")
        print(f"  Process ID     : {info.get('process_id', '?')}")

        mem = client.info("memory")
        used = mem.get("used_memory_human", "?")
        peak = mem.get("used_memory_peak_human", "?")
        print(f"  Memory used    : {used} (peak: {peak})")

        clients = client.info("clients")
        print(f"  Connected      : {clients.get('connected_clients', '?')} clients")
        print(f"  Blocked        : {clients.get('blocked_clients', '?')} clients")

        stats = client.info("stats")
        print(f"  Total commands : {stats.get('total_commands_processed', '?')}")
    except Exception as e:
        print(f"  [!] Could not fetch server info: {e}")


def introspect_keys(client):
    """Test 4: Introspect all gateway-written keys using SCAN (non-blocking)."""
    print_header("4. Key Introspection (what the gateway wrote)")
    try:
        # Use SCAN instead of KEYS * -- non-blocking, production-safe
        all_keys = []
        cursor = 0
        while True:
            cursor, batch = client.scan(cursor=cursor, count=100)
            all_keys.extend(batch)
            if cursor == 0:
                break

        if not all_keys:
            print("  (empty) -- No keys found. Gateway hasn't written anything yet.")
            print("  This is normal if you haven't started the gateway.")
            return

        print(f"  Total keys: {len(all_keys)}  (scanned with SCAN, non-blocking)\n")

        grouped = {}
        for key in sorted(all_keys):
            prefix = key.split(":")[0] if ":" in key else "(ungrouped)"
            grouped.setdefault(prefix, []).append(key)

        for prefix, group_keys in sorted(grouped.items()):
            print(f"  [{prefix}/] ({len(group_keys)} keys)")
            for key in group_keys[:10]:
                key_type = client.type(key)
                ttl = client.ttl(key)
                ttl_str = f"TTL={ttl}s" if ttl > 0 else "no expiry" if ttl == -1 else "expired"

                preview = ""
                if key_type == "string":
                    val = client.get(key)
                    preview = (val[:60] + "...") if val and len(val) > 60 else (val or "")
                elif key_type == "hash":
                    fields = client.hkeys(key)
                    preview = f"{len(fields)} fields: {', '.join(fields[:5])}"
                elif key_type == "list":
                    length = client.llen(key)
                    preview = f"{length} items"
                elif key_type == "set":
                    count = client.scard(key)
                    preview = f"{count} members"
                elif key_type == "zset":
                    count = client.zcard(key)
                    preview = f"{count} members"

                print(f"    [{key_type:6s}] {key}  ({ttl_str})")
                if preview:
                    print(f"              -> {preview}")

            if len(group_keys) > 10:
                print(f"    ... and {len(group_keys) - 10} more")
            print()

    except Exception as e:
        print(f"  [X] Key introspection failed: {e}")


def main():
    print(f"""
+--------------------------------------------------------------+
|           Redis Connection Tester -- Route Mobile            |
+--------------------------------------------------------------+
|  Connecting to: {REDIS_URL:<43s}|
+--------------------------------------------------------------+""")

    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        print(f"\n[X] Could not create Redis client: {e}")
        sys.exit(1)

    # Run tests
    ping_ok = test_ping(client)
    if not ping_ok:
        print(f"""
+--------------------------------------------------------------+
|  [X]  REDIS IS NOT REACHABLE                                 |
+--------------------------------------------------------------+
|  Make sure:                                                  |
|    1. Docker Desktop is running                              |
|    2. Redis container is started:                            |
|       docker compose up redis-cluster -d                     |
|    3. Port 6337 is published to host                         |
|       (check docker-compose.override.yml)                    |
|    4. REDIS_URL in .env is: redis://localhost:6337            |
+--------------------------------------------------------------+""")
        sys.exit(1)

    rw_ok = test_read_write(client)
    test_server_info(client)
    introspect_keys(client)

    # Summary
    print_header("Summary")
    results = [
        ("PING",       ping_ok),
        ("Read/Write", rw_ok),
    ]
    all_ok = all(r[1] for r in results)
    for name, ok in results:
        print(f"  {name:12s} -> {'[OK] PASS' if ok else '[X] FAIL'}")

    if all_ok:
        print(f"""
+--------------------------------------------------------------+
|  [OK]  ALL TESTS PASSED -- Redis is ready!                   |
|                                                              |
|  Your FastAPI gateway will be able to connect.               |
|  Start it with: uvicorn src.main:app --reload                |
+--------------------------------------------------------------+""")
    else:
        print("\n  [!] Some tests failed. Check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
