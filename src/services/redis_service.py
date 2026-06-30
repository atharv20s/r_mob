"""
Enterprise Redis Service
========================
Sections:
    1. Connection
    2. Sessions          (enriched with plan/quota data)
    3. JWT Blacklist
    4. Response Cache    (provider + model scoped keys)
    5. Chat Context      (capped at 40 messages, 1-hour TTL)
    6. Rate Limiter      (fixed-window + sliding-window)
    7. Daily Quota       (INCR counter)
    8. Usage Aggregation (Redis HASH, flushed to SQL every 60 s)
    9. Cache Statistics
   10. Gateway Statistics
   11. Admin Utilities
"""

import redis
import json
import time
import logging
from datetime import datetime, UTC
from typing import Optional, Dict, Any, List

from src.core.config import settings

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("redis_service")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)


class RedisService:
    """
    Enterprise Redis service.
    All public methods are safe to call even when Redis is unavailable;
    they degrade gracefully unless REDIS_REQUIRED=true, in which case
    the application refuses to start without a live connection.
    """

    # =========================================================================
    # 1. Connection
    # =========================================================================

    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self._connect()

    def _connect(self) -> None:
        """Open and validate the Redis connection on startup."""
        try:
            self.client = redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
            self.client.ping()
            self._log("CONNECTION", "OK", f"Redis ready at {settings.REDIS_URL}")
        except Exception as exc:
            if settings.REDIS_REQUIRED:
                self._log("CONNECTION", "FATAL", f"Redis required but unavailable: {exc}")
                raise SystemExit(
                    f"Redis connection failed and REDIS_REQUIRED=true. "
                    f"Cannot start application. Error: {exc}"
                )
            self._log("CONNECTION", "WARN", f"Redis unavailable — running without Redis (dev mode). {exc}")
            self.client = None

    def ping(self) -> bool:
        """Return True if Redis is reachable."""
        if not self.client:
            return False
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    # =========================================================================
    # 2. Sessions  (Hash: HSET / HGETALL / DEL)
    #
    # Enriched session hash stores plan limits alongside identity so that
    # the hot request path (check_rate_limit) never needs a SQL query.
    #
    # session:{user_id}  →  HASH
    #   user_id          int
    #   email            str
    #   role             str   ("admin" | "user")
    #   plan             str   ("free" | "pro" | "enterprise")
    #   rps              int   requests per second
    #   daily_quota      int
    #   monthly_quota    int
    #   login_time       int   epoch seconds
    #   ip               str
    #   user_agent       str
    #   expires          int   epoch seconds
    # =========================================================================

    def create_session(
        self, user_id: int, data: Dict[str, Any], ttl: int = 1800
    ) -> None:
        """Store user session in Redis as a Hash with TTL.

        ``data`` should include identity fields *and* plan fields:
            user_id, email, role, plan, rps, daily_quota,
            monthly_quota, login_time, ip, user_agent, expires
        """
        if not self._require_client("SESSION"):
            return
        key = f"session:{user_id}"
        serialized = {k: str(v) for k, v in data.items()}
        self.client.hset(key, mapping=serialized)
        self.client.expire(key, ttl)
        self._log("SESSION   ", "CREATED", f"session:{user_id}  TTL={ttl}s  data={serialized}")

    def get_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve user session from Redis.  Returns None on miss."""
        if not self._require_client("SESSION"):
            return None
        data = self.client.hgetall(f"session:{user_id}")
        if not data:
            self._log("SESSION   ", "MISS", f"No session found for user={user_id}")
            return None
        # Restore typed integers
        for int_field in ("user_id", "rps", "daily_quota", "monthly_quota",
                          "login_time", "expires"):
            if int_field in data:
                try:
                    data[int_field] = int(data[int_field])
                except ValueError:
                    pass
        self._log("SESSION   ", "HIT ", f"Retrieved session for user={user_id}")
        return data

    def get_session_field(self, user_id: int, field: str) -> Optional[str]:
        """Efficient single-field read via HGET."""
        if not self.client:
            return None
        return self.client.hget(f"session:{user_id}", field)

    def delete_session(self, user_id: int) -> None:
        """Delete user session from Redis (called on logout)."""
        if not self._require_client("SESSION"):
            return
        self.client.delete(f"session:{user_id}")
        self._log("SESSION   ", "DELETED", f"session:{user_id}")

    # =========================================================================
    # 3. JWT Blacklist  (String: SETEX / EXISTS)
    # =========================================================================

    def blacklist_token(self, token: str, expires_in_sec: int) -> None:
        """Blacklist a JWT with TTL equal to remaining token lifetime."""
        if not self._require_client("BLACKLIST"):
            return
        short = token[:16] + "…"
        self.client.setex(f"blacklist:{token}", expires_in_sec, "revoked")
        self._log("BLACKLIST ", "REVOKED", f"token={short}  TTL={expires_in_sec}s")

    def is_blacklisted(self, token: str) -> bool:
        """Return True if the given JWT has been revoked."""
        if not self.client:
            return False
        hit = self.client.exists(f"blacklist:{token}") > 0
        if hit:
            short = token[:16] + "…"
            self._log("BLACKLIST ", "HIT ", f"Revoked token presented: {short}")
        return hit

    # =========================================================================
    # 4. Response Cache  (String: SETEX / GET)
    #
    # Keys are scoped to provider + model so different LLM backends never
    # overwrite each other's entries for the same prompt hash.
    #
    # cache:{provider}:{model}:{sha256}   →  STRING (JSON)
    #   e.g. cache:mistral:mistral-large-latest:abcd1234…
    #        cache:gemini:gemini-1.5-flash:abcd1234…
    #        cache:openai:gpt-4o:abcd1234…
    # =========================================================================

    def cache_response(
        self,
        cache_key: str,
        data: Dict[str, Any],
        expires_in_sec: int = 600,
        provider: str = "default",
        model: str = "default",
    ) -> None:
        """Persist a response payload in the cache with TTL."""
        if not self._require_client("CACHE"):
            return
        full_key = f"cache:{provider}:{model}:{cache_key}"
        self.client.setex(full_key, expires_in_sec, json.dumps(data))
        self._log("CACHE     ", "SAVE", f"{full_key[:36]}…  TTL={expires_in_sec}s")

    def get_cached_response(
        self,
        cache_key: str,
        provider: str = "default",
        model: str = "default",
    ) -> Optional[Dict[str, Any]]:
        """Return a cached response or None on miss."""
        if not self._require_client("CACHE"):
            return None
        full_key = f"cache:{provider}:{model}:{cache_key}"
        raw = self.client.get(full_key)
        if raw:
            self.incr_cache_hit()
            self._log("CACHE     ", "HIT ", f"{full_key[:36]}…")
            return json.loads(raw)
        self.incr_cache_miss()
        self._log("CACHE     ", "MISS", f"{full_key[:36]}…")
        return None

    # =========================================================================
    # 5. Chat Context  (List: LPUSH / LRANGE / LTRIM / DEL)
    #
    # Capped at 40 messages (20 exchanges) with a 1-hour inactivity TTL.
    # context:{user_id}  →  LIST  (newest first via LPUSH)
    # =========================================================================

    def add_context(self, user_id: int, role: str, content: str) -> None:
        """Prepend a message to the user's conversation history (capped at 40)."""
        if not self._require_client("CONTEXT"):
            return
        key = f"context:{user_id}"
        self.client.lpush(key, json.dumps({"role": role, "content": content}))
        self.client.ltrim(key, 0, 39)          # keep last 40 messages (20 exchanges)
        self._log("CONTEXT   ", "ADD ", f"context:{user_id}  role={role}")

    def get_context(self, user_id: int) -> List[Dict[str, Any]]:
        """Return conversation history in chronological order (oldest → newest)."""
        if not self._require_client("CONTEXT"):
            return []
        key = f"context:{user_id}"
        raw_list = self.client.lrange(key, 0, -1)
        parsed: List[Dict[str, Any]] = []
        for item in raw_list:
            try:
                parsed.append(json.loads(item))
            except Exception:
                pass
        return parsed[::-1]   # reverse: LPUSH stores newest first

    def set_context_ttl(self, user_id: int, ttl: int = 3600) -> None:
        """Reset the inactivity TTL on a user's context key (call after each message).

        Default TTL is 3600 seconds (1 hour).  Keys expire automatically when
        the user is inactive, preventing unbounded Redis growth.
        """
        if not self.client:
            return
        self.client.expire(f"context:{user_id}", ttl)

    def clear_context(self, user_id: int) -> None:
        """Wipe a user's conversation history."""
        if not self._require_client("CONTEXT"):
            return
        self.client.delete(f"context:{user_id}")
        self._log("CONTEXT   ", "CLEAR", f"context:{user_id}")

    # =========================================================================
    # 6. Rate Limiter
    #    a) Fixed-window  (String: INCR / EXPIRE)
    #    b) Sliding-window (Sorted Set: ZADD / ZCARD / ZREMRANGEBYSCORE)
    # =========================================================================

    def check_rate_limit(
        self, user_id: str, limit: int = 10, window: int = 1
    ) -> bool:
        """Fixed-window rate limiter. Returns True if request is allowed."""
        if not self.client:
            return True
        key = f"rate_limit:{user_id}"
        current = self.client.incr(key)
        if current == 1:
            self.client.expire(key, window)
        allowed = current <= limit
        if not allowed:
            self._log("RATE LIMIT", "BLOCK", f"user={user_id}  fixed-window  {current}/{limit}")
        return allowed

    def sliding_window_limit(
        self, user_id: str, limit: int = 10, window: int = 1
    ) -> bool:
        """
        Sliding-window rate limiter using a Sorted Set.
        Members are timestamped strings; score = epoch seconds.
        Returns True if the request is within the allowed limit.
        """
        if not self.client:
            return True
        key = f"rate_limit_slide:{user_id}"
        now = time.time()

        # Evict timestamps outside the sliding window
        self.client.zremrangebyscore(key, 0, now - window)
        current_count = self.client.zcard(key)

        if current_count >= limit:
            self._log("RATE LIMIT", "BLOCK", f"user={user_id}  sliding-window  {current_count}/{limit}")
            return False

        # Record this request with a unique member key
        member = f"{now}:{time.perf_counter()}"
        self.client.zadd(key, {member: now})
        self.client.expire(key, window + 5)
        return True

    # =========================================================================
    # 7. Daily Quota  (String: INCR / GET)
    # =========================================================================

    def increment_quota(self, user_id: int, date_str: str) -> int:
        """Increment and return the daily quota counter for user+date."""
        if not self.client:
            return 0
        key = f"quota:{user_id}:{date_str}"
        count = self.client.incr(key)
        if count == 1:
            self.client.expire(key, 172800)   # 48-hour TTL — survives day rollover
        self._log("QUOTA     ", "INCR", f"quota:{user_id}:{date_str}  count={count}")
        return count

    def get_quota(self, user_id: int, date_str: str) -> int:
        """Return current daily quota usage (0 if not yet set)."""
        if not self.client:
            return 0
        val = self.client.get(f"quota:{user_id}:{date_str}")
        return int(val) if val else 0

    # =========================================================================
    # 8. Usage Aggregation  (Hash: HINCRBY / HGETALL)
    #
    # Replaces per-request SQL writes.  The background flusher reads these
    # counters every 60 seconds and upserts them into the UsageRecord table.
    #
    # usage:{user_id}:{date}  →  HASH
    #   request_count    int
    #   input_tokens     int
    #   output_tokens    int
    #   TTL: 172800 s (48 h, survives rollover)
    # =========================================================================

    def increment_usage(
        self,
        user_id: int,
        date_str: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Atomically increment usage counters for user+date in Redis.

        All three counters (request_count, input_tokens, output_tokens) are
        stored in a single HASH so the SQL flusher can read them atomically.
        """
        if not self.client:
            return
        key = f"usage:{user_id}:{date_str}"
        pipe = self.client.pipeline()
        pipe.hincrby(key, "request_count", 1)
        pipe.hincrby(key, "input_tokens", input_tokens)
        pipe.hincrby(key, "output_tokens", output_tokens)
        pipe.expire(key, 172800)   # 48-hour TTL
        pipe.execute()
        self._log(
            "USAGE     ", "INCR",
            f"usage:{user_id}:{date_str}  in={input_tokens}  out={output_tokens}"
        )

    def get_usage(self, user_id: int, date_str: str) -> Dict[str, int]:
        """Return usage counters for user+date (all zeros if not set)."""
        if not self.client:
            return {"request_count": 0, "input_tokens": 0, "output_tokens": 0}
        raw = self.client.hgetall(f"usage:{user_id}:{date_str}")
        return {
            "request_count": int(raw.get("request_count", 0)),
            "input_tokens":  int(raw.get("input_tokens",  0)),
            "output_tokens": int(raw.get("output_tokens", 0)),
        }

    def get_all_usage_keys(self) -> List[str]:
        """Return all active usage:* keys for the SQL flusher to process."""
        if not self.client:
            return []
        return list(self.client.scan_iter("usage:*"))

    # =========================================================================
    # 9. Audit Log Buffer  (List: RPUSH / LRANGE / LTRIM)
    #
    # Replaces per-request SQL writes to the AuditLog table.
    # chat.py calls buffer_audit_log() — O(1), fully async-safe.
    # The background flusher drains the buffer every 60 s.
    #
    # audit:buffer  →  LIST  (right = oldest, left = newest via RPUSH)
    #   Each element: JSON-encoded audit log dict
    #   No TTL — flushed periodically; grows only under flusher failure.
    # =========================================================================

    def buffer_audit_log(self, entry: Dict[str, Any]) -> None:
        """Append an audit log entry to the Redis buffer LIST.

        Args:
            entry: Dict with keys matching AuditLog columns:
                   user_id, endpoint, method, status_code,
                   latency_ms, ip_address, user_agent, request_id
        """
        if not self._require_client("AUDIT"):
            return
        self.client.rpush("audit:buffer", json.dumps(entry))
        self._log("AUDIT     ", "BUFFER", f"user={entry.get('user_id')}  status={entry.get('status_code')}  latency={entry.get('latency_ms')}ms")

    def drain_audit_buffer(self, batch_size: int = 500) -> List[Dict[str, Any]]:
        """Atomically drain up to ``batch_size`` entries from the audit buffer.

        Uses LRANGE + LTRIM so entries are consumed exactly once even if
        multiple flusher instances run concurrently (not typical, but safe).

        Returns a list of decoded audit log dicts.
        """
        if not self.client:
            return []
        # Peek at the first batch_size entries
        raw_entries = self.client.lrange("audit:buffer", 0, batch_size - 1)
        if not raw_entries:
            return []
        # Atomically remove exactly the entries we just read
        self.client.ltrim("audit:buffer", len(raw_entries), -1)
        parsed = []
        for raw in raw_entries:
            try:
                parsed.append(json.loads(raw))
            except Exception:
                pass
        self._log("AUDIT     ", "DRAIN", f"Drained {len(parsed)} audit entries from buffer")
        return parsed

    def audit_buffer_length(self) -> int:
        """Return the current number of buffered audit log entries."""
        if not self.client:
            return 0
        return self.client.llen("audit:buffer")


    def incr_cache_hit(self) -> None:
        if self.client:
            self.client.hincrby("stats:cache", "hits", 1)

    def incr_cache_miss(self) -> None:
        if self.client:
            self.client.hincrby("stats:cache", "misses", 1)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache hit/miss counters and computed hit ratio."""
        if not self.client:
            return {"hits": 0, "misses": 0, "hit_ratio": "0%"}
        raw = self.client.hgetall("stats:cache")
        hits = int(raw.get("hits") or 0)
        misses = int(raw.get("misses") or 0)
        total = hits + misses
        hit_ratio = f"{round(hits / total * 100)}%" if total > 0 else "0%"
        return {"hits": hits, "misses": misses, "hit_ratio": hit_ratio}

    # =========================================================================
    # 11. Gateway Statistics  (Hash: HINCRBY / HGETALL)
    #
    # stats:gateway  →  HASH
    #   requests          total requests processed
    #   cache_hits        prompts served from cache
    #   cache_misses      prompts that hit the LLM
    #   rate_limited      429 responses issued
    #   unauthorized      401 responses issued
    #   errors            500 / LLM errors
    #   total_latency_ms  cumulative LLM latency (for avg calculation)
    # =========================================================================

    def incr_gateway_stat(self, field: str, amount: int = 1) -> None:
        """Atomically increment a gateway statistics counter."""
        if self.client:
            self.client.hincrby("stats:gateway", field, amount)

    def get_gateway_stats(self) -> Dict[str, Any]:
        """Return all gateway statistics with computed derived metrics."""
        if not self.client:
            return {
                "requests": 0, "cache_hits": 0, "cache_misses": 0,
                "rate_limited": 0, "unauthorized": 0, "errors": 0,
                "avg_latency_ms": 0, "hit_ratio": "0%",
            }
        raw = self.client.hgetall("stats:gateway")
        requests      = int(raw.get("requests",         0))
        cache_hits    = int(raw.get("cache_hits",        0))
        cache_misses  = int(raw.get("cache_misses",      0))
        rate_limited  = int(raw.get("rate_limited",      0))
        unauthorized  = int(raw.get("unauthorized",      0))
        errors        = int(raw.get("errors",            0))
        total_latency = int(raw.get("total_latency_ms",  0))

        llm_calls   = cache_misses if cache_misses > 0 else 1  # avoid /0
        total_cache = cache_hits + cache_misses
        hit_ratio   = f"{round(cache_hits / total_cache * 100)}%" if total_cache > 0 else "0%"

        return {
            "requests":       requests,
            "cache_hits":     cache_hits,
            "cache_misses":   cache_misses,
            "rate_limited":   rate_limited,
            "unauthorized":   unauthorized,
            "errors":         errors,
            "avg_latency_ms": round(total_latency / llm_calls),
            "hit_ratio":      hit_ratio,
        }

    # =========================================================================
    # 12. Admin Utilities
    # =========================================================================

    def get_all_keys(self, pattern: str = "*") -> List[str]:
        """Return all keys matching pattern using SCAN (production-safe)."""
        if not self.client:
            return []
        return list(self.client.scan_iter(pattern))

    def get_active_rate_limit_keys(self) -> int:
        """Return count of active rate-limit keys (fixed + sliding)."""
        if not self.client:
            return 0
        count = sum(1 for _ in self.client.scan_iter("rate_limit:*"))
        count += sum(1 for _ in self.client.scan_iter("rate_limit_slide:*"))
        return count

    def redis_info(self) -> Dict[str, Any]:
        """Return high-level Redis server metrics."""
        if not self.client:
            return {"status": "disconnected"}
        try:
            info = self.client.info()
            return {
                "status": "connected",
                "total_keys": self.client.dbsize(),
                "used_memory_human": info.get("used_memory_human", "0B"),
                "redis_version": info.get("redis_version", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "uptime_in_seconds": info.get("uptime_in_seconds", 0),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def inspect_all_keys(self) -> List[Dict[str, Any]]:
        """
        Return a rich view of every key in Redis for the admin portal.
        Each entry includes: name, type, ttl, and a value_preview.
        Uses SCAN (non-blocking) — safe on production.
        """
        if not self.client:
            return []

        entries: List[Dict[str, Any]] = []
        for key in self.client.scan_iter("*"):
            try:
                key_type = self.client.type(key)
                ttl = self.client.ttl(key)

                # Build a concise value preview based on Redis type
                if key_type == "string":
                    raw = self.client.get(key) or ""
                    preview = raw[:120] + ("…" if len(raw) > 120 else "")
                elif key_type == "hash":
                    h = self.client.hgetall(key)
                    preview = json.dumps(h)[:200]
                elif key_type == "list":
                    items = self.client.lrange(key, 0, 4)   # first 5 items
                    preview = json.dumps(items)[:200]
                elif key_type == "zset":
                    members = self.client.zrange(key, 0, 4, withscores=True)
                    preview = json.dumps([{"m": m, "s": round(s, 3)} for m, s in members])[:200]
                elif key_type == "set":
                    members = list(self.client.smembers(key))[:5]
                    preview = json.dumps(members)[:200]
                else:
                    preview = f"<{key_type}>"

                entries.append({
                    "key": key,
                    "type": key_type,
                    "ttl": ttl,          # -1 = no expiry, -2 = key gone
                    "value_preview": preview,
                })
            except Exception:
                pass   # key may have expired between SCAN and TYPE

        # Sort by prefix category for readable grouping
        _order = {
            "session": 0, "blacklist": 1, "cache": 2,
            "context": 3, "quota": 4, "usage": 5,
            "audit": 6, "rate_limit": 7, "stats": 8,
        }
        entries.sort(key=lambda e: _order.get(e["key"].split(":")[0], 99))
        self._log("ADMIN     ", "INSPECT", f"Returned {len(entries)} keys")
        return entries

    def flush_all(self) -> int:
        """
        Delete all keys from the current Redis database.
        Returns the number of keys that existed before the flush.
        ⚠  FOR DEMO / TESTING ONLY — never expose this without auth.
        """
        if not self.client:
            return 0
        count = self.client.dbsize()
        self.client.flushdb()
        self._log("ADMIN     ", "FLUSH", f"Flushed {count} keys from Redis DB")
        return count

    # =========================================================================
    # Internal helpers
    # =========================================================================

    @staticmethod
    def _log(section: str, action: str, detail: str) -> None:
        """Emit a structured, aligned log line."""
        logger.info("[%-10s] [%-7s] %s", section.strip(), action.strip(), detail)

    def _require_client(self, section: str) -> bool:
        """Log and return False when the Redis client is not available."""
        if not self.client:
            self._log(section, "SKIP", "Redis client unavailable — operation skipped.")
            return False
        return True


# Singleton — imported everywhere
redis_service = RedisService()
