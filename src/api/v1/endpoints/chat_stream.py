"""
Chat Completion Streaming Endpoint — Server-Sent Events (SSE)
==============================================================
Provides token-by-token streaming compatible with standard SSE clients.
"""

import json
import time
import datetime
import uuid
import logging
import hashlib
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.core.deps import get_db, get_current_user, check_rate_limit
from src.core.schemas import UserSession
from src.core.prompt_guard import inspect_prompt_safety
from src.core.config import settings
from src.services.redis_service import redis_service
from src.services.ai.router import inference_router

# Prometheus metrics
try:
    from src.core.metrics import REQUEST_COUNT, LATENCY_HISTOGRAM, TOKEN_COUNTER, ERRORS
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False

router = APIRouter()
logger = logging.getLogger("chat_stream")


class ChatStreamRequest(BaseModel):
    prompt: str
    conversation_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.7
    system_prompt: Optional[str] = None
    top_p: Optional[float] = None


def _get_node_id(url: str) -> str:
    """Generate a short stable ID from a node URL."""
    return hashlib.md5(url.encode()).hexdigest()[:8]


@router.post("", response_class=EventSourceResponse)
async def chat_streaming(
    payload: ChatStreamRequest,
    request: Request,
    user: UserSession = Depends(get_current_user),
    _rate_limit: None = Depends(check_rate_limit),
):
    """
    Stream a response to the client using Server-Sent Events (SSE).
    """
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty.",
        )

    # ── 1. Prompt Safety Check ───────────────────────────────────────────
    if getattr(settings, "PROMPT_SAFETY_ENABLED", True):
        try:
            inspect_prompt_safety(prompt)
        except HTTPException:
            redis_service.incr_gateway_stat("prompt_blocked")
            raise

    # ── 2. Resolve provider + model ──────────────────────────────────────
    provider = (payload.provider or getattr(settings, "DEFAULT_PROVIDER", "ollama")).lower()
    model = payload.model or getattr(settings, "DEFAULT_MODEL", "llama3:latest")
    from src.services.model_registry import model_registry
    model = model_registry.resolve_model_name(model)

    # ── 3. Resolve conversation ──────────────────────────────────────────
    conversation_id = payload.conversation_id
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        redis_service.link_conversation_to_user(
            user.id, conversation_id, title=f"Chat {conversation_id[:8]}"
        )
    elif not redis_service.user_owns_conversation(user.id, conversation_id):
        redis_service.link_conversation_to_user(
            user.id, conversation_id, title=f"Chat {conversation_id[:8]}"
        )

    # ── 4. Retrieve history & get adapter ────────────────────────────────
    try:
        adapter = inference_router.get_adapter(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # Resolve active requests tracking node URL if clustered
    node_url = getattr(adapter, "base_url", None)
    node_id = _get_node_id(node_url) if node_url else "cloud"

    history = redis_service.get_conversation_history(conversation_id)
    messages = history + [{"role": "user", "content": prompt}]

    # Metadata
    ip_address = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "unknown")
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    today_str = datetime.date.today().isoformat()

    # Metrics
    redis_service.incr_gateway_stat("requests")
    if METRICS_AVAILABLE:
        REQUEST_COUNT.labels(provider=provider, model=model, status="processing").inc()

    # Track active requests for scheduling
    if node_url:
        redis_service.increment_active_requests(provider, node_id)

    async def event_generator():
        start_time = time.time()
        full_response = []
        token_count = 0
        
        try:
            generator = adapter.generate_stream(
                prompt=prompt,
                model_name=model,
                messages=messages,
                temperature=payload.temperature,
            )
            
            async for token in generator:
                if token:
                    full_response.append(token)
                    token_count += 1
                    # Yield standard SSE data packet
                    yield {
                        "event": "message",
                        "data": json.dumps({"token": token, "done": False})
                    }

            # Finalize stream
            latency_ms = int((time.time() - start_time) * 1000)
            redis_service.incr_gateway_stat("total_latency_ms", latency_ms)
            
            # Simple token estimation for billing/usage
            prompt_tokens = len(prompt.split()) * 2  # raw estimate
            completion_tokens = token_count
            total_tokens = prompt_tokens + completion_tokens

            # Record usage
            redis_service.increment_usage(
                user_id=user.id,
                date_str=today_str,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
            )
            redis_service.increment_quota(user.id, today_str)

            if METRICS_AVAILABLE:
                TOKEN_COUNTER.labels(model=model).inc(total_tokens)
                LATENCY_HISTOGRAM.labels(layer=f"inference_stream_{provider}").observe(
                    time.time() - start_time
                )

            # Update conversation history
            redis_service.add_conversation_message(conversation_id, "user", prompt)
            redis_service.add_conversation_message(
                conversation_id, "assistant", "".join(full_response)
            )
            redis_service.set_conversation_ttl(conversation_id)

            # Audit buffer log
            redis_service.buffer_audit_log({
                "user_id": user.id,
                "endpoint": "/api/v1/chat/stream",
                "method": "POST",
                "status_code": 200,
                "latency_ms": latency_ms,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "request_id": request_id,
            })

            # Yield final completion metadata
            yield {
                "event": "done",
                "data": json.dumps({
                    "conversation_id": conversation_id,
                    "done": True,
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens
                    }
                })
            }

        except Exception as e:
            logger.error(f"Error in streaming generation loop: {e}")
            if METRICS_AVAILABLE:
                ERRORS.inc()
            redis_service.incr_gateway_stat("errors")
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)})
            }
        finally:
            # Decrement active requests telemetry counter
            if node_url:
                redis_service.decrement_active_requests(provider, node_id)

    return EventSourceResponse(event_generator())
