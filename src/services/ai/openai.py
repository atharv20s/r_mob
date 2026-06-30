from typing import Dict, Any, Optional
from openai import AsyncOpenAI
from src.core.config import settings
from src.services.ai.base import BaseAIService

class OpenAIService(BaseAIService):
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.client = None
        has_credentials = self.api_key and self.api_key != "placeholder_openai_key"
        if has_credentials:
            self.client = AsyncOpenAI(api_key=self.api_key)

    async def generate_text(self, prompt: str, model: Optional[str] = None, messages: Optional[list] = None) -> Dict[str, Any]:
        if not self.client:
            model_name = model or "gpt-4o-mini"
            return {
                "success": True,
                "provider": "openai (mock)",
                "text": f"Mock response for prompt: '{prompt}' using OpenAI. (OpenAI API key is not configured)",
                "model": model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }

        try:
            model_name = model or "gpt-4o-mini"
            msgs = messages if messages else [{"role": "user", "content": prompt}]
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=msgs
            )
            return {
                "success": True,
                "provider": "openai",
                "text": response.choices[0].message.content,
                "model": model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
