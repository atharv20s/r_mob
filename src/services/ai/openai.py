import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
from openai import AsyncOpenAI
from src.core.config import settings
from src.services.ai.base import BaseInferenceAdapter

class OpenAIAdapter(BaseInferenceAdapter):
    """Adapter for the OpenAI Cloud API."""

    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.client = None
        self.provider_name = "openai"
        has_credentials = self.api_key and self.api_key != "placeholder_openai_key" and "placeholder" not in self.api_key.lower()
        if has_credentials:
            self.client = AsyncOpenAI(api_key=self.api_key)

    async def generate(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate response using OpenAI API."""
        model = model_name or "gpt-4o-mini"
        if not self.client:
            return {
                "success": True,
                "provider": f"{self.provider_name} (mock)",
                "text": f"Mock response for prompt: '{prompt}' using OpenAI. (OpenAI API key is not configured)",
                "model": model,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }

        try:
            msgs = messages if messages else [{"role": "user", "content": prompt}]
            response = await self.client.chat.completions.create(
                model=model,
                messages=msgs,
                temperature=temperature
            )
            
            prompt_tokens = response.usage.prompt_tokens if response.usage else 0
            completion_tokens = response.usage.completion_tokens if response.usage else 0
            total_tokens = response.usage.total_tokens if response.usage else 0

            return {
                "success": True,
                "provider": self.provider_name,
                "text": response.choices[0].message.content,
                "model": model,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens
                }
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
        """Stream responses from OpenAI API."""
        model = model_name or "gpt-4o-mini"
        if not self.client:
            mock_tokens = ["Hello", " from", " OpenAI", " mock", " stream!"]
            for token in mock_tokens:
                yield token
                await asyncio.sleep(0.1)
            return

        try:
            msgs = messages if messages else [{"role": "user", "content": prompt}]
            stream = await self.client.chat.completions.create(
                model=model,
                messages=msgs,
                temperature=temperature,
                stream=True
            )
            async for chunk in stream:
                if chunk.choices:
                    token = chunk.choices[0].delta.content or ""
                    if token:
                        yield token
        except Exception as e:
            yield f"Error in OpenAI stream: {str(e)}"

    async def health_check(self) -> Dict[str, Any]:
        """OpenAI cloud service health check."""
        if not self.client:
            return {"status": "unconfigured", "provider": self.provider_name}
        return {"status": "healthy", "provider": self.provider_name}


# Legacy compatibility alias
OpenAIService = OpenAIAdapter
