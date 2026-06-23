import redis
import json
import time
from typing import Optional, Dict, Any
from src.core.config import settings

class RedisService:
    def __init__(self):
        self.client = None
        self.fallback_cache: Dict[str, str] = {}
        self.fallback_blacklist = set()
        self.fallback_rate_limits: Dict[str, list] = {}  # key -> list of timestamps
        
        try:
            self.client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            self.client.ping()
            print(f"Successfully connected to Redis at {settings.REDIS_URL}")
        except Exception as e:
            print(f"Redis is not available: {e}. Falling back to in-memory store.")
            self.client = None

    # --- Blacklist ---
    def blacklist_token(self, token: str, expires_in_sec: int) -> None:
        if self.client:
            try:
                self.client.setex(f"blacklist:{token}", expires_in_sec, "true")
            except Exception as e:
                print(f"Redis blacklist error: {e}")
                self.fallback_blacklist.add(token)
        else:
            self.fallback_blacklist.add(token)

    def is_token_blacklisted(self, token: str) -> bool:
        if self.client:
            try:
                return self.client.exists(f"blacklist:{token}") > 0
            except Exception as e:
                print(f"Redis blacklist check error: {e}")
                return token in self.fallback_blacklist
        else:
            return token in self.fallback_blacklist

    # --- Rate Limiting ---
    def is_allowed(self, user_id: str, limit: int = 10, window: int = 1) -> bool:
        """Rate limiter: checks if user has exceeded 'limit' requests in 'window' seconds."""
        key = f"rate_limit:{user_id}"
        if self.client:
            try:
                current = self.client.incr(key)
                if current == 1:
                    self.client.expire(key, window)
                return current <= limit
            except Exception as e:
                print(f"Redis rate limiting error: {e}")
                # Fall back to in-memory limits
        
        # In-memory sliding window fallback for robustness
        now = time.time()
        timestamps = self.fallback_rate_limits.setdefault(key, [])
        # filter timestamps within the window
        timestamps = [t for t in timestamps if now - t < window]
        if len(timestamps) < limit:
            timestamps.append(now)
            self.fallback_rate_limits[key] = timestamps
            return True
        return False

    # --- Response Caching ---
    def get_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        if self.client:
            try:
                cached_val = self.client.get(f"cache:{cache_key}")
                if cached_val:
                    return json.loads(cached_val)
            except Exception as e:
                print(f"Redis cache get error: {e}")
                if cache_key in self.fallback_cache:
                    return json.loads(self.fallback_cache[cache_key])
                return None
        else:
            if cache_key in self.fallback_cache:
                return json.loads(self.fallback_cache[cache_key])
        return None

    def set_cache(self, cache_key: str, data: Dict[str, Any], expires_in_sec: int = 300) -> None:
        serialized = json.dumps(data)
        if self.client:
            try:
                self.client.setex(f"cache:{cache_key}", expires_in_sec, serialized)
            except Exception as e:
                print(f"Redis cache set error: {e}")
                self.fallback_cache[cache_key] = serialized
        else:
            self.fallback_cache[cache_key] = serialized

redis_service = RedisService()
