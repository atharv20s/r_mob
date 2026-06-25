import redis
import json
import time
from datetime import datetime, UTC
from typing import Optional, Dict, Any
from src.core.config import settings


class RedisService:
    """Enterprise Redis service — no in-memory fallback when REDIS_REQUIRED=true."""

    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self._connect()

    def _connect(self):
        try:
            self.client = redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
            self.client.ping()
            print(f"[OK] Redis connected at {settings.REDIS_URL}")
        except Exception as e:
            if settings.REDIS_REQUIRED:
                print(f"[FATAL] Redis is required but unavailable: {e}")
                raise SystemExit(
                    f"Redis connection failed and REDIS_REQUIRED=true. "
                    f"Cannot start application. Error: {e}"
                )
            else:
                print(f"[WARN] Redis unavailable: {e}. Running without Redis (dev mode).")
                self.client = None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        """Return True if Redis is reachable."""
        if not self.client:
            return False
        try:
            return self.client.ping()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # JWT Blacklist
    # ------------------------------------------------------------------
    def blacklist_token(self, token: str, expires_in_sec: int) -> None:
        """Blacklist a JWT token with TTL equal to remaining token lifetime."""
        self.client.setex(f"blacklist:{token}", expires_in_sec, "true")

    def is_token_blacklisted(self, token: str) -> bool:
        """Check if a JWT token is blacklisted."""
        return self.client.exists(f"blacklist:{token}") > 0

    # ------------------------------------------------------------------
    # Rate Limiting  (INCR + EXPIRE pattern)
    # ------------------------------------------------------------------
    def is_allowed(self, user_id: str, limit: int = 10, window: int = 1) -> bool:
        """Rate limiter: allow up to `limit` requests in `window` seconds."""
        key = f"rate_limit:{user_id}"
        current = self.client.incr(key)
        if current == 1:
            self.client.expire(key, window)
        return current <= limit

    def get_active_rate_limit_keys(self) -> int:
        """Return count of active rate_limit:* keys."""
        keys = self.client.keys("rate_limit:*")
        return len(keys)

    # ------------------------------------------------------------------
    # Response Caching
    # ------------------------------------------------------------------
    def get_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached response. Returns None on miss."""
        cached_val = self.client.get(f"cache:{cache_key}")
        if cached_val:
            self.incr_cache_hit()
            return json.loads(cached_val)
        self.incr_cache_miss()
        return None

    def set_cache(
        self, cache_key: str, data: Dict[str, Any], expires_in_sec: int = 600
    ) -> None:
        """Store response in cache with TTL."""
        serialized = json.dumps(data)
        self.client.setex(f"cache:{cache_key}", expires_in_sec, serialized)

    # ------------------------------------------------------------------
    # Cache Statistics
    # ------------------------------------------------------------------
    def incr_cache_hit(self) -> None:
        self.client.incr("stats:cache_hits")

    def incr_cache_miss(self) -> None:
        self.client.incr("stats:cache_misses")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache hit/miss statistics."""
        hits = int(self.client.get("stats:cache_hits") or 0)
        misses = int(self.client.get("stats:cache_misses") or 0)
        total = hits + misses
        hit_ratio = f"{round(hits / total * 100)}%" if total > 0 else "0%"
        return {"hits": hits, "misses": misses, "hit_ratio": hit_ratio}

    # ------------------------------------------------------------------
    # Session Store
    # ------------------------------------------------------------------
    def set_session(
        self, user_id: int, data: Dict[str, Any], ttl: int = 86400
    ) -> None:
        """Store user session in Redis with 24h default TTL."""
        self.client.setex(
            f"session:{user_id}", ttl, json.dumps(data)
        )

    def get_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve user session from Redis."""
        val = self.client.get(f"session:{user_id}")
        if val:
            return json.loads(val)
        return None

    # ------------------------------------------------------------------
    # Daily Quota Enforcement
    # ------------------------------------------------------------------
    def increment_quota(self, user_id: int, date_str: str) -> int:
        """Increment daily quota counter. Returns new count."""
        key = f"quota:{user_id}:{date_str}"
        count = self.client.incr(key)
        if count == 1:
            # Set TTL of 48h on first increment so key auto-expires
            self.client.expire(key, 172800)
        return count

    def get_quota(self, user_id: int, date_str: str) -> int:
        """Get current daily quota usage."""
        val = self.client.get(f"quota:{user_id}:{date_str}")
        return int(val) if val else 0

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def get_all_keys(self, pattern: str = "*") -> list:
        """Return all keys matching pattern (for diagnostics)."""
        return self.client.keys(pattern)


redis_service = RedisService()
