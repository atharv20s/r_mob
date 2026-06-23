from typing import Dict, Any, Optional
from google import genai
from src.core.config import settings
from src.services.ai.base import BaseAIService

class GeminiService(BaseAIService):
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.client = None
        has_credentials = self.api_key and self.api_key != "placeholder_gemini_key"
        if has_credentials:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"Failed to initialize Gemini client: {e}")

    async def generate_text(self, prompt: str, model: Optional[str] = None) -> Dict[str, Any]:
        model_name = model or settings.DEFAULT_MODEL
        if not self.client:
            return {
                "success": True,
                "provider": "gemini (mock)",
                "text": f"Mock response for prompt: '{prompt}' using model: '{model_name}'. (Gemini API key is not configured)",
                "model": model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }

        try:
            # google-genai package uses aio namespace for async calls
            response = await self.client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return {
                "success": True,
                "provider": "gemini",
                "text": response.text,
                "model": model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
