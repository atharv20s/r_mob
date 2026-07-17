"""
OpenAI-Compatible Chat Completions Endpoint
===========================================
Drop-in replacement for OpenAI SDK clients.
Paths:
  - POST /v1/chat/completions
"""

import json
import time
import uuid
import logging
import hashlib
import datetime
from typing import List, Dict, Any, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.core.deps import get_current_user, check_rate_limit
from src.core.schemas import UserSession
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
logger = logging.getLogger("openai_compat")


# ── OpenAI Schemas ──────────────────────────────────────────────────────────

class OpenAIMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class OpenAIChatRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    user: Optional[str] = None


class OpenAIChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class OpenAIChoice(BaseModel):
    index: int = 0
    message: OpenAIChoiceMessage
    finish_reason: str = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAIChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[OpenAIChoice]
    usage: OpenAIUsage


# ── Internal Helper ─────────────────────────────────────────────────────────

def _get_node_id(url: str) -> str:
    """Generate a short stable ID from a node URL."""
    return hashlib.md5(url.encode()).hexdigest()[:8]


# ── Endpoint ────────────────────────────────────────────────────────────────

@router.post("/chat/completions")
async def openai_chat_completions(
    payload: OpenAIChatRequest,
    request: Request,
    user: UserSession = Depends(get_current_user),
    _rate_limit: None = Depends(check_rate_limit),
):
    """
    Standard OpenAI-compatible chat completions endpoint.
    Supports streaming and non-streaming modes.
    """
    # Verify input messages are non-empty
    if not payload.messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="messages list cannot be empty.",
        )

    # 1. Resolve provider & model
    from src.services.model_registry import model_registry
    model = model_registry.resolve_model_name(payload.model)
    registry_meta = model_registry.get_model(model)
    
    if registry_meta:
        provider = registry_meta.get("provider", "ollama")
    else:
        # Simple heuristic mappings
        model_lower = model.lower()
        if "gpt" in model_lower:
            provider = "openai"
        elif "gemini" in model_lower:
            provider = "gemini"
        elif "mistral" in model_lower:
            provider = "mistral"
        else:
            provider = getattr(settings, "DEFAULT_PROVIDER", "ollama")

    # 2. Get adapter
    try:
        adapter = inference_router.get_adapter(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    node_url = getattr(adapter, "base_url", None)
    node_id = _get_node_id(node_url) if node_url else "cloud"

    # Convert OpenAIMessage to simple dict formats
    messages_dict = [{"role": msg.role, "content": msg.content} for msg in payload.messages]
    last_prompt = messages_dict[-1]["content"] if messages_dict else ""

    # Increment statistics
    redis_service.incr_gateway_stat("requests")

    # ── 3. Streaming Mode (SSE) ──────────────────────────────────────────────
    if payload.stream:
        if node_url:
            redis_service.increment_active_requests(provider, node_id)

        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created_time = int(time.time())

        async def stream_generator():
            start_time = time.time()
            full_response = []
            token_count = 0
            
            try:
                # First chunk with role setup
                yield {
                    "data": json.dumps({
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"role": "assistant", "content": ""},
                            "finish_reason": None
                        }]
                    })
                }

                generator = adapter.generate_stream(
                    prompt=last_prompt,
                    model_name=model,
                    messages=messages_dict,
                    temperature=payload.temperature or 0.7,
                )
                
                async for token in generator:
                    if token:
                        full_response.append(token)
                        token_count += 1
                        yield {
                            "data": json.dumps({
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": token},
                                    "finish_reason": None
                                }]
                            })
                        }

                # Final chunk indicating finish_reason
                yield {
                    "data": json.dumps({
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop"
                        }]
                    })
                }

                # OpenAI client expects a literal "[DONE]" string as the final stream frame
                yield {"data": "[DONE]"}

                # Record stats and billing after generator yields finish
                latency_ms = int((time.time() - start_time) * 1000)
                redis_service.incr_gateway_stat("total_latency_ms", latency_ms)
                
                prompt_tokens = len(last_prompt.split()) * 2
                completion_tokens = token_count
                total_tokens = prompt_tokens + completion_tokens

                redis_service.increment_usage(
                    user_id=user.id,
                    date_str=datetime.date.today().isoformat(),
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                )
                if METRICS_AVAILABLE:
                    TOKEN_COUNTER.labels(model=model).inc(total_tokens)
                    LATENCY_HISTOGRAM.labels(layer=f"openai_compat_stream_{provider}").observe(
                        time.time() - start_time
                    )

            except Exception as e:
                logger.error(f"Error in OpenAI-compatible stream: {e}")
                if METRICS_AVAILABLE:
                    ERRORS.inc()
                redis_service.incr_gateway_stat("errors")
                yield {"data": json.dumps({"error": str(e)})}
            finally:
                if node_url:
                    redis_service.decrement_active_requests(provider, node_id)

        return EventSourceResponse(stream_generator())

    # ── 4. Synchronous Mode ──────────────────────────────────────────────────
    else:
        if node_url:
            redis_service.increment_active_requests(provider, node_id)

        start_time = time.time()
        try:
            res = await adapter.generate(
                prompt=last_prompt,
                model_name=model,
                messages=messages_dict,
                temperature=payload.temperature or 0.7,
            )
        finally:
            if node_url:
                redis_service.decrement_active_requests(provider, node_id)

        latency_ms = int((time.time() - start_time) * 1000)
        redis_service.incr_gateway_stat("total_latency_ms", latency_ms)

        if not res.get("success", False):
            redis_service.incr_gateway_stat("errors")
            if METRICS_AVAILABLE:
                ERRORS.inc()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=res.get("error", "Failed to generate completion from inference engine"),
            )

        text = res.get("text", "")
        usage_dict = res.get("usage", {})
        prompt_tokens = usage_dict.get("prompt_tokens", len(last_prompt.split()) * 2)
        completion_tokens = usage_dict.get("completion_tokens", len(text.split()) * 2)
        total_tokens = usage_dict.get("total_tokens", prompt_tokens + completion_tokens)

        # Record usage
        redis_service.increment_usage(
            user_id=user.id,
            date_str=datetime.date.today().isoformat(),
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

        if METRICS_AVAILABLE:
            TOKEN_COUNTER.labels(model=model).inc(total_tokens)
            LATENCY_HISTOGRAM.labels(layer=f"openai_compat_{provider}").observe(
                time.time() - start_time
            )

        # Return standard OpenAI response envelope
        return OpenAIChatResponse(
            model=model,
            choices=[
                OpenAIChoice(
                    index=0,
                    message=OpenAIChoiceMessage(content=text),
                    finish_reason="stop"
                )
            ],
            usage=OpenAIUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens
            )
        )
