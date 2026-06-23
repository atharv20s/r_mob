from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.services.ai.factory import AIProviderFactory

router = APIRouter()

class PromptRequest(BaseModel):
    prompt: str
    provider: Optional[str] = "gemini"
    model: Optional[str] = None

@router.post("/generate")
async def generate_text(payload: PromptRequest):
    """Generate text using AI providers (Gemini, OpenAI, or Mistral)."""
    try:
        service = AIProviderFactory.get(payload.provider)
        res = await service.generate_text(
            prompt=payload.prompt,
            model=payload.model
        )
        if not res["success"]:
            raise HTTPException(status_code=400, detail=res["error"])
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
