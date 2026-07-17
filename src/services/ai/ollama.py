"""
Ollama LLM Adapter
==================
Connects to a local or Docker-networked Ollama instance via its REST API.

Default endpoint:
    Docker:  http://ollama-cluster:11434
    Local:   http://localhost:11434

The adapter calls /api/generate with stream=false or stream=true.
Conforms to BaseInferenceAdapter interface.
"""

import json
import httpx
import asyncio
import logging
import time
from typing import Dict, Any, Optional, List, AsyncGenerator

from src.core.config import settings
from src.services.ai.base import BaseInferenceAdapter

logger = logging.getLogger("ai.ollama")


class OllamaAdapter(BaseInferenceAdapter):
    """Adapter for the Ollama local inference engine pool."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.provider_name = "ollama"

    async def generate(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        max_retries: int = 3,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generate a completion from Ollama.
        """
        model = model_name or getattr(settings, "DEFAULT_MODEL", "llama3.1:8b")
        
        # Build the full prompt from conversation history if provided
        if messages:
            compiled_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                compiled_parts.append(f"{role}: {content}")
            full_prompt = "\n".join(compiled_parts)
        else:
            full_prompt = prompt

        payload = {
            "model": model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }

        backoff_factor = 2.0
        timeout = httpx.Timeout(timeout=120.0, connect=10.0)

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/api/generate",
                        json=payload,
                    )

                if response.status_code == 404:
                    return {
                        "success": False,
                        "error": f"Model '{model}' not found in Ollama. Run: ollama pull {model}",
                        "provider": self.provider_name,
                    }

                if response.status_code >= 500:
                    sleep_time = backoff_factor ** attempt
                    logger.warning(
                        "Ollama server error (%d). Retrying in %.1fs...",
                        response.status_code, sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                response.raise_for_status()
                data = response.json()

                response_text = data.get("response", "")
                eval_count = data.get("eval_count", 0)
                prompt_eval_count = data.get("prompt_eval_count", 0)
                total_tokens = eval_count + prompt_eval_count

                return {
                    "success": True,
                    "text": response_text,
                    "tokens_used": total_tokens,
                    "provider": self.provider_name,
                    "model": data.get("model", model),
                    "usage": {
                        "prompt_tokens": prompt_eval_count,
                        "completion_tokens": eval_count,
                        "total_tokens": total_tokens,
                    },
                    "duration_ns": data.get("total_duration", 0),
                }

            except httpx.TimeoutException:
                sleep_time = backoff_factor ** attempt
                logger.warning(
                    "Ollama timeout (attempt %d/%d). Retrying in %.1fs...",
                    attempt + 1, max_retries, sleep_time,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(sleep_time)
                else:
                    return {
                        "success": False,
                        "error": "Ollama request timed out after all retries",
                        "provider": self.provider_name,
                    }
            except httpx.ConnectError:
                return {
                    "success": False,
                    "error": f"Cannot connect to Ollama at {self.base_url}. Is it running?",
                    "provider": self.provider_name,
                }
            except Exception as exc:
                return {
                    "success": False,
                    "error": str(exc),
                    "provider": self.provider_name,
                }

        return {
            "success": False,
            "error": "Max retries exceeded for Ollama",
            "provider": self.provider_name,
        }

    async def generate_stream(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Stream tokens from Ollama.
        """
        model = model_name or getattr(settings, "DEFAULT_MODEL", "llama3.1:8b")
        
        if messages:
            compiled_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                compiled_parts.append(f"{role}: {content}")
            full_prompt = "\n".join(compiled_parts)
        else:
            full_prompt = prompt

        payload = {
            "model": model,
            "prompt": full_prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
            },
        }

        timeout = httpx.Timeout(timeout=120.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as response:
                    if response.status_code != 200:
                        yield f"Error: Received status code {response.status_code}"
                        return
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                token = data.get("response", "")
                                if token:
                                    yield token
                            except Exception:
                                pass
        except Exception as e:
            yield f"Error in stream generation: {str(e)}"

    async def health_check(self) -> Dict[str, Any]:
        """Check if Ollama is reachable and list available models."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("name", "unknown") for m in data.get("models", [])]
                    return {
                        "status": "healthy",
                        "provider": self.provider_name,
                        "base_url": self.base_url,
                        "available_models": models,
                    }
                return {"status": "unhealthy", "provider": self.provider_name, "error": f"HTTP {response.status_code}"}
        except Exception as exc:
            return {"status": "unreachable", "provider": self.provider_name, "error": str(exc)}

    async def list_models(self) -> List[str]:
        """Return a list of model names available in this Ollama instance."""
        health = await self.health_check()
        return health.get("available_models", [])
