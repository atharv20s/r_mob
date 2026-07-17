"""
Chat Completion Endpoint — Provider-Agnostic + Conversation-Based
=================================================================

Request flow:
    1. JWT Auth + Rate Limit (deps)
    2. Prompt Safety Check (prompt_guard)
    3. Conversation Context Retrieval (Redis conversation:{id})
    4. Provider-Agnostic Inference (InferenceRouter)
    5. Prometheus Metrics Update
    6. Redis State Updates (usage, audit, conversation, cache)
    7. Response

Supports:
    - Multiple providers: ollama, vllm, mistral, openai, gemini
    - Multiple models per provider: gemma2:2b, mistral, gpt-4o, etc.
    - ChatGPT-style conversation threads via conversation_id
    - Response caching scoped by provider + model + params
"""

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
from src.core.prompt_guard import inspect_prompt_safety
from src.core.config import settings
from src.services.redis_service import redis_service
from src.services.ai.router import inference_router

# Prometheus metrics (imported conditionally for flexibility)
try:
    from src.core.metrics import (
        REQUEST_COUNT, LATENCY_HISTOGRAM, TOKEN_COUNTER,
        CACHE_HITS, CACHE_MISSES, ERRORS, PROMPT_INJECTION_BLOCKED,
    )
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False

router = APIRouter()


class ChatRequest(BaseModel):
    prompt: str
    conversation_id: Optional[str] = None   # auto-generated if missing
    provider: Optional[str] = None          # defaults to settings.DEFAULT_PROVIDER
    model: Optional[str] = None             # defaults to settings.DEFAULT_MODEL
    temperature: float = 0.7
    system_prompt: Optional[str] = None
    top_p: Optional[float] = None


class ChatResponseUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    model: str
    provider: str
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
    The background flusher syncs these to SQL every 60 s.
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
    """Push an audit log entry onto the Redis audit:buffer LIST."""
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


def _get_node_id(url: str) -> str:
    """Generate a short stable ID from a node URL."""
    return hashlib.md5(url.encode()).hexdigest()[:8]


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

    Flow:
        1. Prompt safety check (injection filter)
        2. Resolve provider + model
        3. Resolve/create conversation
        4. Cache lookup
        5a. Cache HIT  → return cached response
        5b. Cache MISS → route to inference adapter → return AI response
        6. Update conversation context, usage, audit, cache
    """
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty.",
        )

    # ── 1. Prompt Safety — BEFORE any GPU compute ────────────────────────
    if getattr(settings, "PROMPT_SAFETY_ENABLED", True):
        try:
            inspect_prompt_safety(prompt)
        except HTTPException:
            if METRICS_AVAILABLE:
                PROMPT_INJECTION_BLOCKED.inc()
            redis_service.incr_gateway_stat("prompt_blocked")
            raise

    # ── 2. Resolve provider + model ──────────────────────────────────────
    provider = (payload.provider or getattr(settings, "DEFAULT_PROVIDER", "ollama")).lower()
    model = payload.model or getattr(settings, "DEFAULT_MODEL", "llama3:latest")
    from src.services.model_registry import model_registry
    model = model_registry.resolve_model_name(model)

    # ── 3. Resolve / create conversation ─────────────────────────────────
    conversation_id = payload.conversation_id
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        # Auto-create and link to user
        redis_service.link_conversation_to_user(
            user.id, conversation_id, title=f"Chat {conversation_id[:8]}"
        )
    elif not redis_service.user_owns_conversation(user.id, conversation_id):
        # Auto-link if conversation_id provided but not yet linked
        redis_service.link_conversation_to_user(
            user.id, conversation_id, title=f"Chat {conversation_id[:8]}"
        )

    # ── Request metadata ─────────────────────────────────────────────────
    ip_address = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent")
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    today_str  = datetime.date.today().isoformat()

    # ── Gateway stats ────────────────────────────────────────────────────
    redis_service.incr_gateway_stat("requests")
    if METRICS_AVAILABLE:
        REQUEST_COUNT.labels(provider=provider, model=model, status="processing").inc()

    # ── 4. Split cache lookup (conversation → global) ────────────────────
    cache_key   = _build_cache_key(
        prompt=prompt,
        temperature=payload.temperature,
        system_prompt=payload.system_prompt,
        top_p=payload.top_p,
    )

    # 4a. Check conversation-scoped cache first (most specific)
    cached_data = redis_service.get_conversation_cached_response(
        conversation_id=conversation_id, cache_key=cache_key
    )

    # 4b. Fall back to global shared cache
    if not cached_data:
        cached_data = redis_service.get_global_cached_response(
            cache_key=cache_key, provider=provider, model=model
        )

    # 4c. Legacy cache fallback (backward compatibility)
    if not cached_data:
        cached_data = redis_service.get_cached_response(
            cache_key=cache_key, provider=provider, model=model
        )

    if cached_data:
        # ── Cache HIT ────────────────────────────────────────────────────
        redis_service.incr_gateway_stat("cache_hits")
        if METRICS_AVAILABLE:
            CACHE_HITS.inc()

        _record_usage(user.id, today_str, 0, 0)
        _buffer_audit(user.id, 200, 1, ip_address, user_agent, request_id)

        # Store in conversation context
        redis_service.add_conversation_message(conversation_id, "user", prompt)
        redis_service.add_conversation_message(conversation_id, "assistant", cached_data.get("response", ""))
        redis_service.set_conversation_ttl(conversation_id)

        cached_data["cached"] = True
        cached_data["conversation_id"] = conversation_id
        cached_data["provider"] = provider
        return cached_data

    # ── 5. Cache MISS — route to inference ───────────────────────────────
    redis_service.incr_gateway_stat("cache_misses")
    if METRICS_AVAILABLE:
        CACHE_MISSES.inc()

    # ── 5a. Per-model rate limit (Tier 6) ────────────────────────────────
    from src.core.rate_limiter import enforce_rate_limits
    try:
        enforce_rate_limits(
            user_id=user.id,
            ip_address=ip_address,
            model=model,
            rps=getattr(user, "rps", 5),
        )
    except Exception:
        pass  # Already handled by the rate limiter raising HTTPException

    # Get adapter from inference router
    try:
        adapter = inference_router.get_adapter(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # Build messages from conversation history
    history  = redis_service.get_conversation_history(conversation_id)
    messages = history + [{"role": "user", "content": prompt}]

    # Resolve active requests tracking node URL if clustered
    node_url = getattr(adapter, "base_url", None)
    node_id = _get_node_id(node_url) if node_url else "cloud"

    if node_url:
        redis_service.increment_active_requests(provider, node_id)

    start_time = time.time()
    try:
        res = await adapter.generate(
            prompt=prompt,
            model_name=model,
            messages=messages,
            temperature=payload.temperature,
        )
    finally:
        if node_url:
            redis_service.decrement_active_requests(provider, node_id)
            
    latency_ms = int((time.time() - start_time) * 1000)

    # Track latency
    redis_service.incr_gateway_stat("total_latency_ms", latency_ms)
    if METRICS_AVAILABLE:
        LATENCY_HISTOGRAM.labels(layer=f"inference_{provider}").observe(
            (time.time() - start_time)
        )

    if not res.get("success", False):
        redis_service.incr_gateway_stat("errors")
        if METRICS_AVAILABLE:
            ERRORS.inc()
        _buffer_audit(user.id, 400, latency_ms, ip_address, user_agent, request_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("error", f"Error from {provider} inference"),
        )

    # ── 6. Record usage ─────────────────────────────────────────────────
    usage = res.get("usage", {})
    prompt_t     = usage.get("prompt_tokens",     0)
    completion_t = usage.get("completion_tokens",  0)
    total_t      = usage.get("total_tokens",       0)

    _record_usage(user.id, today_str, prompt_t, completion_t)

    if METRICS_AVAILABLE:
        TOKEN_COUNTER.labels(model=model).inc(total_t)

    # ── 7. Audit buffer ──────────────────────────────────────────────────
    _buffer_audit(user.id, 200, latency_ms, ip_address, user_agent, request_id)

    # ── 8. Update conversation context ───────────────────────────────────
    redis_service.add_conversation_message(conversation_id, "user", prompt)
    redis_service.add_conversation_message(conversation_id, "assistant", res.get("text", ""))
    redis_service.set_conversation_ttl(conversation_id)

    # ── 9. Build and cache response (split cache write) ──────────────────
    response_payload = {
        "response":        res.get("text", ""),
        "conversation_id": conversation_id,
        "model":           res.get("model", model),
        "provider":        provider,
        "cached":          False,
        "usage": {
            "prompt_tokens":     prompt_t,
            "completion_tokens": completion_t,
            "total_tokens":      total_t,
        },
    }

    # Write to conversation cache (context-dependent)
    redis_service.cache_conversation_response(
        conversation_id=conversation_id,
        cache_key=cache_key,
        data=response_payload,
        expires_in_sec=settings.CONVERSATION_CACHE_TTL,
    )

    # Write to global cache (shared — benefits other users with same prompt)
    redis_service.cache_global_response(
        cache_key=cache_key,
        data=response_payload,
        provider=provider,
        model=model,
        expires_in_sec=settings.GLOBAL_CACHE_TTL,
    )

    return response_payload
