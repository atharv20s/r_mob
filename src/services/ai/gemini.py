import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
from google import genai
from src.core.config import settings
from src.services.ai.base import BaseInferenceAdapter

class GeminiAdapter(BaseInferenceAdapter):
    """Adapter for Google Gemini API."""

    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.client = None
        self.provider_name = "gemini"
        has_credentials = self.api_key and self.api_key != "placeholder_gemini_key" and "placeholder" not in self.api_key.lower()
        if has_credentials:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"Failed to initialize Gemini client: {e}")

    async def generate(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate response using Gemini API."""
        model = model_name or "gemini-1.5-flash"
        if not self.client:
            return {
                "success": True,
                "provider": f"{self.provider_name} (mock)",
                "text": f"Mock response for prompt: '{prompt}' using model: '{model}'. (Gemini API key is not configured)",
                "model": model,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }

        try:
            if messages:
                contents = []
                for m in messages:
                    role = "user" if m["role"] == "user" else "model"
                    contents.append({"role": role, "parts": [{"text": m["content"]}]})
            else:
                contents = prompt

            response = await self.client.aio.models.generate_content(
                model=model,
                contents=contents,
            )
            return {
                "success": True,
                "provider": self.provider_name,
                "text": response.text,
                "model": model,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def generate_stream(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Stream responses from Gemini API."""
        model = model_name or "gemini-1.5-flash"
        if not self.client:
            mock_tokens = ["Hello", " from", " Gemini", " mock", " stream!"]
            for token in mock_tokens:
                yield token
                await asyncio.sleep(0.1)
            return

        try:
            if messages:
                contents = []
                for m in messages:
                    role = "user" if m["role"] == "user" else "model"
                    contents.append({"role": role, "parts": [{"text": m["content"]}]})
            else:
                contents = prompt

            stream = await self.client.aio.models.generate_content_stream(
                model=model,
                contents=contents,
            )
            async for chunk in stream:
                token = chunk.text or ""
                if token:
                    yield token
        except Exception as e:
            yield f"Error in Gemini stream: {str(e)}"

    async def health_check(self) -> Dict[str, Any]:
        """Gemini cloud service health check."""
        if not self.client:
            return {"status": "unconfigured", "provider": self.provider_name}
        return {"status": "healthy", "provider": self.provider_name}


# Legacy compatibility alias
GeminiService = GeminiAdapter
