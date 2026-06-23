import httpx
import asyncio
from typing import Dict, Any, Optional
from src.core.config import settings
from src.services.ai.base import BaseAIService

class MistralService(BaseAIService):
    def __init__(self):
        self.api_key = settings.MISTRAL_API_KEY
        self.api_url = "https://api.mistral.ai/v1/chat/completions"

    async def generate_text(self, prompt: str, model: Optional[str] = None) -> Dict[str, Any]:
        if not self.api_key or self.api_key == "your_mistral_api_key":
            return {"success": False, "error": "Mistral API key is not configured"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        model_name = model or "mistral-large-latest"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }

        max_retries = 3
        backoff_factor = 2.0
        # connect timeout = 5s, read timeout = 30s
        timeout = httpx.Timeout(timeout=30.0, connect=5.0)

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(self.api_url, json=payload, headers=headers)
                
                if response.status_code == 429:
                    sleep_time = backoff_factor ** attempt
                    print(f"Mistral API rate limit hit (429). Retrying in {sleep_time}s...")
                    await asyncio.sleep(sleep_time)
                    continue
                elif response.status_code >= 500:
                    sleep_time = backoff_factor ** attempt
                    print(f"Mistral API server error ({response.status_code}). Retrying in {sleep_time}s...")
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
                    "provider": "mistral",
                    "text": text,
                    "model": response_json.get("model", model_name),
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0)
                    }
                }
            except httpx.TimeoutException:
                sleep_time = backoff_factor ** attempt
                print(f"Mistral API timeout. Retrying in {sleep_time}s...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(sleep_time)
                else:
                    return {"success": False, "error": "Mistral API request timed out after retries"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Max retries exceeded for Mistral API"}
