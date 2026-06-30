from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import hashlib
import time
import datetime
import uuid

from src.core.deps import get_db, get_current_user, check_rate_limit
from src.core.schemas import UserSession
from src.services.redis_service import redis_service
from src.services.ai.factory import AIProviderFactory

router = APIRouter()

# Provider / model constants — used for scoped cache keys
_PROVIDER = "mistral"
_MODEL    = "mistral-large-latest"


class ChatRequest(BaseModel):
    prompt: str
    temperature: float = 0.7
    system_prompt: Optional[str] = None
    top_p: Optional[float] = None


class ChatResponseUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    response: str
    model: str
    cached: bool
    usage: ChatResponseUsage


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_cache_key(
    prompt: str,
    temperature: float,
    system_prompt: Optional[str],
    top_p: Optional[float],
) -> str:
    """
    Build a deterministic SHA-256 cache key from all parameters that affect
    LLM output.  Provider and model are applied as key prefixes by
    redis_service, so they don't need to be included in the hash itself.

    Hashing:
        temperature  | system_prompt (empty string if None) | top_p | prompt

    This prevents two requests with the same prompt but different settings
    from incorrectly sharing a cached response.
    """
    top_p_str = f"{top_p:.4f}" if top_p is not None else "none"
    canonical = (
        f"{temperature:.4f}"
        f"|{system_prompt or ''}"
        f"|{top_p_str}"
        f"|{prompt}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_usage(
    user_id: int,
    date_str: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """
    Atomically increment Redis usage counters.

    Replaces the per-request SQLAlchemy UsageRecord upsert.
    The background flusher (usage_flusher.py) syncs these to SQL every 60 s.
    Also increments the daily quota counter used by check_rate_limit().
    """
    redis_service.increment_usage(
        user_id=user_id,
        date_str=date_str,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    redis_service.increment_quota(user_id, date_str)


def _buffer_audit(
    user_id: int,
    status_code: int,
    latency_ms: int,
    ip_address: str,
    user_agent: str,
    request_id: str,
) -> None:
    """
    Push an audit log entry onto the Redis audit:buffer LIST.

    Replaces the per-request AuditLog SQL write.
    The background flusher drains this buffer and bulk-inserts into SQL.
    """
    redis_service.buffer_audit_log({
        "user_id":    user_id,
        "endpoint":   "/api/v1/chat",
        "method":     "POST",
        "status_code": status_code,
        "latency_ms": latency_ms,
        "ip_address": ip_address,
        "user_agent": user_agent or "",
        "request_id": request_id,
    })


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=ChatResponse)
async def chat_completion(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: UserSession = Depends(get_current_user),
    _rate_limit: None = Depends(check_rate_limit),
):
    """
    Send a message to the AI gateway.

    Cache key:  SHA-256(temperature | system_prompt | top_p | prompt)
    Cache scope: per provider + model (prefix: cache:{provider}:{model}:{hash})

    Flow:
        1. Gateway stat increment
        2. Cache lookup  (fully-qualified key including generation params)
        3a. Cache HIT  → Redis usage INCR → audit buffer → context update → return
        3b. Cache MISS → LLM call → Redis usage INCR → audit buffer
                       → context update → cache store → return
    """
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty."
        )

    # ── Request metadata ─────────────────────────────────────────────────
    ip_address = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent")
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    today_str  = datetime.date.today().isoformat()

    # ── Gateway stats: count every request ──────────────────────────────
    redis_service.incr_gateway_stat("requests")

    # ── 1. Cache lookup — key includes all generation parameters ─────────
    cache_key   = _build_cache_key(
        prompt=prompt,
        temperature=payload.temperature,
        system_prompt=payload.system_prompt,
        top_p=payload.top_p,
    )
    cached_data = redis_service.get_cached_response(
        cache_key=cache_key, provider=_PROVIDER, model=_MODEL
    )

    if cached_data:
        # ── Cache HIT ────────────────────────────────────────────────────
        redis_service.incr_gateway_stat("cache_hits")

        # Record usage (zero tokens — served from cache)
        _record_usage(user.id, today_str, 0, 0)

        # Audit buffer (1 ms latency — no LLM was called)
        _buffer_audit(user.id, 200, 1, ip_address, user_agent, request_id)

        # Store in conversation context + refresh TTL
        redis_service.add_context(user.id, "user", prompt)
        redis_service.add_context(user.id, "assistant", cached_data.get("response", ""))
        redis_service.set_context_ttl(user.id)

        cached_data["cached"] = True
        return cached_data

    # ── 2. Cache MISS — call LLM ─────────────────────────────────────────
    redis_service.incr_gateway_stat("cache_misses")

    mistral_service = AIProviderFactory.get("mistral")

    # Build messages from conversation context
    history  = redis_service.get_context(user.id)
    messages = history + [{"role": "user", "content": prompt}]

    start_time = time.time()
    res        = await mistral_service.generate_text(prompt=prompt, messages=messages)
    latency_ms = int((time.time() - start_time) * 1000)

    # Track total LLM latency for avg calculation
    redis_service.incr_gateway_stat("total_latency_ms", latency_ms)

    if not res.get("success", False):
        redis_service.incr_gateway_stat("errors")
        _buffer_audit(user.id, 400, latency_ms, ip_address, user_agent, request_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("error", "Error requesting Mistral API response")
        )

    # ── 3. Record usage in Redis (replaces per-request SQL write) ────────
    tokens       = res.get("usage", {})
    prompt_t     = tokens.get("prompt_tokens",     0)
    completion_t = tokens.get("completion_tokens",  0)
    total_t      = tokens.get("total_tokens",       0)

    _record_usage(user.id, today_str, prompt_t, completion_t)

    # ── 4. Audit buffer (replaces per-request SQL write) ─────────────────
    _buffer_audit(user.id, 200, latency_ms, ip_address, user_agent, request_id)

    # ── 5. Update conversation context + refresh TTL ──────────────────────
    redis_service.add_context(user.id, "user",      prompt)
    redis_service.add_context(user.id, "assistant", res.get("text", ""))
    redis_service.set_context_ttl(user.id)

    # ── 6. Build and cache response ──────────────────────────────────────
    response_payload = {
        "response": res.get("text", ""),
        "model":    res.get("model", _MODEL),
        "cached":   False,
        "usage": {
            "prompt_tokens":     prompt_t,
            "completion_tokens": completion_t,
            "total_tokens":      total_t,
        },
    }

    redis_service.cache_response(
        cache_key=cache_key,
        data=response_payload,
        expires_in_sec=600,
        provider=_PROVIDER,
        model=_MODEL,
    )

    return response_payload
