from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
import hashlib
import time
import datetime

from src.db.models import User, AuditLog, UsageRecord
from src.core.deps import get_db, get_current_user, check_rate_limit
from src.services.redis_service import redis_service
from src.services.ai_service import ai_service

router = APIRouter()

class ChatRequest(BaseModel):
    prompt: str

class ChatResponseUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatResponse(BaseModel):
    response: str
    model: str
    cached: bool
    usage: ChatResponseUsage

@router.post("", response_model=ChatResponse)
def chat_completion(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _rate_limit: None = Depends(check_rate_limit)
):
    """
    Send messages directly to Mistral AI with authentication,
    rate limiting, caching, and database audit logs.
    """
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty."
        )

    # 1. Search for response in Redis cache
    prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    cache_key = f"chat:{prompt_hash}"
    
    cached_data = redis_service.get_cache(cache_key)
    if cached_data:
        # Cache hit — log to audit
        audit_log = AuditLog(
            user_id=user.id,
            endpoint="/api/v1/chat",
            method="POST",
            status_code=200,
            latency_ms=1
        )
        db.add(audit_log)
        
        # Increment usage
        today_str = datetime.date.today().isoformat()
        usage_rec = db.query(UsageRecord).filter(UsageRecord.user_id == user.id, UsageRecord.date == today_str).first()
        if not usage_rec:
            usage_rec = UsageRecord(user_id=user.id, date=today_str, request_count=1)
            db.add(usage_rec)
        else:
            usage_rec.request_count += 1
        
        db.commit()
        
        cached_data["cached"] = True
        return cached_data

    # 2. Cache miss — call Mistral API
    start_time = time.time()
    res = ai_service.generate_text(prompt=prompt, provider="mistral")
    latency_ms = int((time.time() - start_time) * 1000)

    if not res.get("success", False):
        audit_log = AuditLog(
            user_id=user.id,
            endpoint="/api/v1/chat",
            method="POST",
            status_code=400,
            latency_ms=latency_ms
        )
        db.add(audit_log)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("error", "Error requesting Mistral API response")
        )

    # 3. Log audit
    audit_log = AuditLog(
        user_id=user.id,
        endpoint="/api/v1/chat",
        method="POST",
        status_code=200,
        latency_ms=latency_ms
    )
    db.add(audit_log)

    # 4. Log token usage
    today_str = datetime.date.today().isoformat()
    usage_rec = db.query(UsageRecord).filter(UsageRecord.user_id == user.id, UsageRecord.date == today_str).first()
    
    tokens = res.get("usage", {})
    prompt_t = tokens.get("prompt_tokens", 0)
    completion_t = tokens.get("completion_tokens", 0)
    total_t = tokens.get("total_tokens", 0)

    if not usage_rec:
        usage_rec = UsageRecord(
            user_id=user.id,
            date=today_str,
            request_count=1,
            input_tokens=prompt_t,
            output_tokens=completion_t,
            cost=0.0
        )
        db.add(usage_rec)
    else:
        usage_rec.request_count += 1
        usage_rec.input_tokens += prompt_t
        usage_rec.output_tokens += completion_t

    db.commit()

    # 5. Build response
    response_payload = {
        "response": res.get("text", ""),
        "model": res.get("model", "mistral-large-latest"),
        "cached": False,
        "usage": {
            "prompt_tokens": prompt_t,
            "completion_tokens": completion_t,
            "total_tokens": total_t
        }
    }

    # 6. Cache in Redis (5 min TTL)
    redis_service.set_cache(cache_key, response_payload, expires_in_sec=300)

    return response_payload
