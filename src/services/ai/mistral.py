import httpx
import json
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
from src.core.config import settings
from src.services.ai.base import BaseInferenceAdapter

class MistralAdapter(BaseInferenceAdapter):
    """Adapter for the Mistral Cloud API."""

    def __init__(self):
        self.api_key = settings.MISTRAL_API_KEY
        self.api_url = "https://api.mistral.ai/v1/chat/completions"
        self.provider_name = "mistral"

    async def generate(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        max_retries: int = 3,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate a response using Mistral Cloud API."""
        if not self.api_key or self.api_key == "your_mistral_api_key" or "placeholder" in self.api_key.lower():
            return {"success": False, "error": "Mistral API key is not configured"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        model = model_name or "mistral-large-latest"
        msgs = messages if messages else [{"role": "user", "content": prompt}]
        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature
        }

        backoff_factor = 2.0
        timeout = httpx.Timeout(timeout=30.0, connect=5.0)

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(self.api_url, json=payload, headers=headers)
                
                if response.status_code == 429:
                    sleep_time = backoff_factor ** attempt
                    await asyncio.sleep(sleep_time)
                    continue
                elif response.status_code >= 500:
                    sleep_time = backoff_factor ** attempt
                    await asyncio.sleep(sleep_time)
                    continue

                response_json = response.json()
                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": response_json.get("message", response_json.get("error", {}).get("message", "Error from Mistral API")),
                        "status_code": response.status_code
                    }

                choices = response_json.get("choices", [])
                text = choices[0].get("message", {}).get("content", "") if choices else ""
                usage = response_json.get("usage", {})
                
                return {
                    "success": True,
                    "provider": self.provider_name,
                    "text": text,
                    "model": response_json.get("model", model),
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0)
                    }
                }
            except httpx.TimeoutException:
                sleep_time = backoff_factor ** attempt
                if attempt < max_retries - 1:
                    await asyncio.sleep(sleep_time)
                else:
                    return {"success": False, "error": "Mistral API request timed out after retries"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Max retries exceeded for Mistral API"}

    async def generate_stream(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Stream responses from Mistral Cloud API."""
        if not self.api_key or self.api_key == "your_mistral_api_key" or "placeholder" in self.api_key.lower():
            yield "Error: Mistral API key is not configured"
            return

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        model = model_name or "mistral-large-latest"
        msgs = messages if messages else [{"role": "user", "content": prompt}]
        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "stream": True,
        }

        timeout = httpx.Timeout(timeout=30.0, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", self.api_url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        yield f"Error: Received status code {response.status_code}"
                        return
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                choices = data.get("choices", [])
                                token = choices[0].get("delta", {}).get("content", "") if choices else ""
                                if token:
                                    yield token
                            except Exception:
                                pass
        except Exception as e:
            yield f"Error in stream generation: {str(e)}"

    async def health_check(self) -> Dict[str, Any]:
        """Mistral cloud service health check."""
        if not self.api_key or self.api_key == "your_mistral_api_key" or "placeholder" in self.api_key.lower():
            return {"status": "unconfigured", "provider": self.provider_name}
        return {"status": "healthy", "provider": self.provider_name}


# Legacy compatibility alias
MistralService = MistralAdapter
