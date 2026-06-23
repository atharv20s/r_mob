from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.services.ai_service import ai_service

router = APIRouter()

class PromptRequest(BaseModel):
    prompt: str
    provider: Optional[str] = "gemini"
    model: Optional[str] = None

@router.post("/generate")
def generate_text(payload: PromptRequest):
    """Generate text using AI providers (Gemini or OpenAI)."""
    res = ai_service.generate_text(
        prompt=payload.prompt,
        provider=payload.provider,
        model=payload.model
    )
    if not res["success"]:
        raise HTTPException(status_code=400, detail=res["error"])
    return res
